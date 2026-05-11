from fastapi import FastAPI
import httpx
from pydantic import BaseModel
from asyncio import sleep
import requests 
import httpx


app = FastAPI()

class User(BaseModel):
    name:str
    age:int


@app.get("/")
async def route():
    response = requests.get("https://google.com")

@app.post("/user")
def create_user(user: User):
    return {"message": f"User {user.name} created successfully!"}


@app.get("/sync")
def sync_route():
    return {"type": "sync"}

@app.get("/async")
async def async_route():
    await sleep(1)  # Simulate an asynchronous operation
    return {"type": "async"}