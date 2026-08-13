-- Seeded analysis fixture; consumes what warehouse-sql produces.
-- This statement is the cross-repository seam: analytics.daily_revenue is written by
-- one repository and read by this one, so the two graphs join on it.
INSERT INTO risk.customer_features (customer_id, lifetime_value)
SELECT customer_id, SUM(gross_revenue)
FROM analytics.daily_revenue
GROUP BY customer_id;
