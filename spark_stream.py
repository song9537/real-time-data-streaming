import logging
import uuid

from cassandra.cluster import Cluster
from pyspark.sql import SparkSession
from pyspark.sql.functions import from_json, col
from pyspark.sql.types import StructType, StructField, StringType


def create_keyspace(session):
    session.execute(
        """
        CREATE KEYSPACE IF NOT EXISTS spark_streams
        WITH replication = {'class': 'SimpleStrategy', 'replication_factor': '1'};
        """
    )
    print("Keyspace created successfully")


def create_table(session):
    session.execute(
        """
    CREATE TABLE IF NOT EXISTS spark_streams.created_users (
        id UUID PRIMARY KEY,
        first_name TEXT,
        last_name TEXT,
        gender TEXT,
        address TEXT,
        post_code TEXT,
        email TEXT,
        username TEXT,
        dob TEXT,
        registered_date TEXT,
        phone TEXT,
        picture TEXT
    );
    """
    )

    print("Table created successfully")


def insert_data(session, df_batch, batch_id):

    for row in df_batch.collect():
        print("inserting data for row: {row.asDict()}")

        kwargs = row.asDict()

        print(f"kwargs: {kwargs}")

        # Ensure the id is present and convert to UUID
        if "id" not in kwargs or not kwargs.get("id"):
            raise ValueError("ID is missing from the provided data.")

        try:
            # Convert id string back to UUID before inserting into Cassandra
            user_id = uuid.UUID(kwargs.get("id"))  # Convert back to UUID
        except ValueError as e:
            raise ValueError(f"Invalid UUID format for id: {kwargs.get('id')}") from e

        first_name = kwargs.get("first_name")
        last_name = kwargs.get("last_name")
        gender = kwargs.get("gender")
        address = kwargs.get("address")
        postcode = kwargs.get("postcode")
        email = kwargs.get("email")
        username = kwargs.get("username")
        dob = kwargs.get("dob")
        registered_date = kwargs.get("registered_date")
        phone = kwargs.get("phone")
        picture = kwargs.get("picture")

        try:
            session.execute(
                """
                INSERT INTO spark_streams.created_users(id, first_name, last_name, gender, address, 
                    post_code, email, username, dob, registered_date, phone, picture)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
                (
                    user_id,
                    first_name,
                    last_name,
                    gender,
                    address,
                    postcode,
                    email,
                    username,
                    dob,
                    registered_date,
                    phone,
                    picture,
                ),
            )
            logging.info(f"Data inserted for {first_name} {last_name}")

        except Exception as e:
            logging.error(f"could not insert data due to {e}")


def create_spark_connection():
    s_conn = None

    try:
        s_conn = (
            SparkSession.builder.appName("SparkDataStreaming")
            .config(
                "spark.jars.packages",
                "com.datastax.spark:spark-cassandra-connector_2.12:3.4.1,"
                "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.2",
            )
            .config("spark.cassandra.connection.host", "localhost")
            .getOrCreate()
        )

        s_conn.sparkContext.setLogLevel("ERROR")
        logging.info("Spark connection created successfully!")
    except Exception as e:
        logging.error(f"Couldn't create the spark session due to exception {e}")

    return s_conn


def connect_to_kafka(spark_conn):
    spark_df = None
    try:
        spark_df = (
            spark_conn.readStream.format("kafka")
            .option("kafka.bootstrap.servers", "localhost:9092")
            .option("subscribe", "users_created")
            .option("startingOffsets", "earliest")
            .load()
        )
        logging.info("kafka dataframe created successfully")
    except Exception as e:
        logging.warning(f"kafka dataframe could not be created because: {e}")

    return spark_df


def create_cassandra_connection():
    session = None
    try:
        # connecting to the cassandra cluster
        cluster = Cluster(["localhost"])
        session = cluster.connect()
        return session
    except Exception as e:
        logging.error("Could not create cassandra connection due to {e}")
        return None


def create_selection_df_from_kafka(spark_df):
    schema = StructType(
        [
            StructField("id", StringType(), False),
            StructField("first_name", StringType(), False),
            StructField("last_name", StringType(), False),
            StructField("gender", StringType(), False),
            StructField("address", StringType(), False),
            StructField("post_code", StringType(), False),
            StructField("email", StringType(), False),
            StructField("username", StringType(), False),
            StructField("dob", StringType(), False),
            StructField("registered_date", StringType(), False),
            StructField("phone", StringType(), False),
            StructField("picture", StringType(), False),
        ]
    )

    sel = (
        spark_df.selectExpr("CAST(value AS STRING)")
        .select(from_json(col("value"), schema).alias("data"))
        .select("data.*")
    )
    print(sel)

    return sel


if __name__ == "__main__":
    # create spark connection
    spark_conn = create_spark_connection()

    if spark_conn is not None:
        # connect to kafka with spark connection
        spark_df = connect_to_kafka(spark_conn)
        selection_df = create_selection_df_from_kafka(spark_df)
        session = create_cassandra_connection()

        if session is not None:
            create_keyspace(session)
            create_table(session)

            # Use foreachBatch to process each batch of the stream
            query = selection_df.writeStream.foreachBatch(
                lambda df_batch, batch_id: insert_data(session, df_batch, batch_id)
            ).start()
            query.awaitTermination()
        else:
            logging.error("Cassandra session creation failed")
