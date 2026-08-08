"""
Locust Workload Generator Script for TrainTicket Core Booking Flow.
Simulates realistic user load across train search, user login, order reservation, and payment APIs.
"""

from locust import HttpUser, task, between
import random

class TrainTicketUser(HttpUser):
    wait_time = between(1, 3)

    @task(4)
    def search_trains(self):
        self.client.get("/api/v1/trainservice/trains")

    @task(3)
    def user_login(self):
        self.client.post("/api/v1/userservice/users/login", json={
            "username": "fdse_microservices",
            "password": "DefaultPassword"
        })

    @task(2)
    def query_orders(self):
        self.client.get("/api/v1/orderservice/order")

    @task(1)
    def query_station(self):
        self.client.get("/api/v1/stationservice/stations")
