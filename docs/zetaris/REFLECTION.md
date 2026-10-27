# Reflecting on the Zetaris Unified Semantic Layer

This answers part 2 of Zetaris's "Build with Zetaris" criteria. Part 1 (use case, sources, exploratory queries, the USL and its DDL, queries showing it's active, and the data-quality rule) is in `docs/zetaris/USL.md`.

## 2a. How the USL saved time compared with a hand-coded approach

**What we built both ways.** Pricise needs one answer per product: *which offers are new, full-price, from a credible store with an established domain, and how do they compare with prices we've seen before?* Before the USL, the Price Agent answered that in Python. That code still exists as the fallback, so we can compare the two directly. That comparison is the bonus item: a hand-coded part compared with the USL.

| | Hand-coded (Python fallback) | Zetaris USL |
|---|---|---|
| Domain age for six retailers | 28 lines: an RDAP client per lookup, parsing, error handling | Six `CREATE LIGHTNING REST TABLE` statements plus one view |
| Safe-price logic | 190 lines: joins, deduplication, medians, safety flags | One `safe_price` table in SQL |
| Data-quality check | Filters scattered through the agent code | One rule: `REGISTER DQ new_full_price … AS price > 0 AND item_condition = 'new' AND full_price = 1` |
| **Total** | **≈ 218 lines** | **91 lines of SQL + 1 rule** |

**Where the time went down:**
- **New sources took one statement each.** RDAP and Google Shopping became queryable tables without our writing an HTTP client, retry logic or a parser for each.
- **One query joins a database with two web APIs.** The Postgres history, the live offers and the domain records sit in the same `SELECT`.
- **Data quality is declared once, not re-implemented.** The rule runs inside Zetaris and lists the failing rows (79 in the latest run), so we didn't have to build our own reporting.
- **The logic is readable by anyone who knows SQL.** A teammate can check what "safe" means in one place.

**Where it cost us time:**
- **COMPILE USL rejected column names that are SQL keywords** (`condition`, `source`, `domain`) with a general "Invalid DDL statement" error. Finding the cause took two attempts.
- **Renaming those columns silently broke the agent's own query.** Zetaris rejected it, and the agent fell back to Python on every check until we noticed. A contract test now checks the agent's query against the USL's declared columns.
- **Every read recomputes the view**, taking about 5 seconds for one product, so the USL read needed a longer timeout than our other calls.

Overall the USL cut our data-access code by about 58% (218 lines down to 92: 91 lines of SQL plus the one DQ rule). The hours were spent learning the DDL rules rather than writing plumbing.

## 2b. How the USL would help, or not help, in my work

At CGI I work on a multi-agent tool that turns financial statements into annual reports. Its agents handle extraction, calculation, reconciliation and templating.

**Where a USL would help:**
- **Reconciliation is a cross-source join problem.** The numbers come from several systems that name the same things differently. One semantic table per reporting line would give the reconciliation agent a single, consistent view instead of a connector per system.
- **Data-quality rules fit accounting checks naturally.** Statements must balance and totals must equal the sum of their lines. Declaring those as DQ rules, with the failing records listed, is exactly the evidence auditors ask for.
- **Shared definitions.** The calculation and templating agents would read the same definition of each figure, so they can't drift apart.

**Where it might not help:**
- **Data location rules.** Public-sector and financial data often has strict rules about where it may be stored and processed, which could limit where the USL itself can run.
- **Latency.** A view that recomputes on every read, as ours did, is slow for interactive use unless it's materialised.
- **Changing source schemas.** When a source system changes, the USL definitions need maintaining too. Our rename bug showed the queries that depend on them need contract tests.

**Bonus: a forecast.** If data access in that tool were similar to Pricise's, the same ratio would mean roughly half the integration code per new source system. That's a rough forecast from one hackathon project, not a measurement. It would need checking against the tool's real connectors before anyone relied on it.

## 2c. How to make the USL stronger: a product requirement

### Requirement: Local USL caching and refresh

**Problem.** In a local Docker install, `CACHE TABLE` works for source tables but not for USL tables. The only documented way to precompute a USL table is `MATERIALIZE USL TABLE … TO STORAGE`, which needs a cloud storage target and a storage setting id, and there's no SQL command to list those. So in local and hackathon installs every read of a USL table recomputes the whole view: about 5 seconds for one product in Pricise. That's too slow for an agent that answers while a user waits.

**User story.** As a developer building an agent on Zetaris locally, I want to cache a USL table on the same machine and refresh it on a schedule, so my agent reads it in well under a second and I can see how fresh the data is.

**Requirements**
1. Cache a USL table to local storage with one statement, with no cloud storage target needed.
2. Optional scheduled refresh (e.g. every 6 hours) and a manual refresh statement.
3. Each cached table exposes its last refresh time, so apps can show how old the data is.
4. Reads fall back to computing the view when the cache is missing or expired, and say so in the response metadata.
5. Data-quality rules run on refresh, and their results are stored with that refresh.

**Acceptance criteria**
- In a clean local install, a cached USL read returns in under 1 second for about 1,000 rows.
- The refresh time can be queried with SQL.
- A failed refresh keeps serving the last good cache and reports the error.

### Bonus: mock of the proposed feature

The syntax below is **proposed, not real Zetaris syntax**. It shows how the Pricise USL would use the feature.

```sql
-- proposed: cache a USL table locally and refresh it every 6 hours
CACHE USL TABLE lightning.metastore.pricise.pricise_usl.safe_price
  REFRESH EVERY 6 HOURS;

-- proposed: read the cache's freshness
SELECT table_name, last_refreshed, row_count, dq_invalid_rows
FROM lightning.metastore.usl_cache_status
WHERE table_name = 'pricise_usl.safe_price';

-- proposed: refresh now
REFRESH USL TABLE lightning.metastore.pricise.pricise_usl.safe_price;
```

**Two smaller requests from our build:**
- **Name the keyword in DDL errors.** For example: "Invalid DDL statement: `condition` is a reserved word (column 14); quote it or rename it."
- **Validate queries against the USL schema.** A read-only check that tells an app its query uses a column the USL doesn't declare. This would have caught our rename bug before it reached users.
