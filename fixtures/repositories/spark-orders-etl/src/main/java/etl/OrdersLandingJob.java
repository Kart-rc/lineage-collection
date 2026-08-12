package etl;

import org.apache.spark.sql.Dataset;
import org.apache.spark.sql.Row;
import org.apache.spark.sql.SparkSession;

/** Lands a Kafka topic into the object store: the topic -> s3 hop of the estate. */
public class OrdersLandingJob {

    public void run(SparkSession spark) {
        Dataset<Row> events = spark.readStream()
            .format("kafka")
            .option("subscribe", "inventory-count-events")
            .load();

        events.writeStream()
            .format("parquet")
            .option("path", "s3://raw-lake/raw/events/orders")
            .start();
    }
}
