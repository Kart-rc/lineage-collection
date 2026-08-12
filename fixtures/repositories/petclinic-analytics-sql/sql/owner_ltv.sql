-- Derives an analytics snapshot from the OLTP tables the Petclinic service writes.
-- This is the cross-plane seam: a Java service writes `owners`, and this pipeline
-- derives from the same dataset, so the two repositories join on it.
INSERT INTO analytics.owner_ltv (owner_id, home_city)
SELECT id, city
FROM owners;
