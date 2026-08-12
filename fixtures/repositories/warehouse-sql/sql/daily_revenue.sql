-- Seeded analysis fixture; statements are declarations for the static analyzer.
INSERT INTO analytics.daily_revenue (customer_id, gross_revenue, revenue_date)
SELECT customer_id, SUM(amount), DATE(occurred_at)
FROM raw.transactions
GROUP BY customer_id, DATE(occurred_at);
