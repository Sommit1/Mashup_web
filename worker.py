import os
from redis import Redis
from rq import Worker, Queue, Connection

listen = ["default"]
conn = Redis.from_url(os.environ["REDIS_URL"])

if __name__ == "__main__":
    with Connection(conn):
        Worker(map(Queue, listen)).work()
