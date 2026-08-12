package etl;

import org.apache.spark.sql.Dataset;
import org.apache.spark.sql.Row;
import org.apache.spark.sql.SparkSession;
import org.apache.spark.sql.functions;

public class OrdersCurationJob {

    public void run(SparkSession spark) {
        Dataset<Row> raw = spark.read().parquet("s3://raw-lake/raw/events/orders");

        Dataset<Row> curated = raw
            .withColumn("order_id", functions.col("order_id"))
            .withColumn("customer_id", functions.col("customer_id"))
            .withColumn("order_total", functions.sum(functions.col("amount")))
            .withColumn("order_date", functions.to_date(functions.col("occurred_at")));

        curated.write().parquet("s3://raw-lake/curated/orders");
    }

    public void dynamicSink(SparkSession spark, String suffix) {
        Dataset<Row> raw = spark.read().parquet("s3://raw-lake/raw/events/orders");
        raw.write().parquet("s3://raw-lake/curated/" + suffix);
    }
}
