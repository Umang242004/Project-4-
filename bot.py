import os
import requests
import tweepy
from datetime import datetime

# 🔑 Twitter API Auth
client = tweepy.Client(
    consumer_key=os.getenv("TW_CONSUMER_KEY"),
    consumer_secret=os.getenv("TW_CONSUMER_SECRET"),
    access_token=os.getenv("TW_ACCESS_TOKEN"),
    access_token_secret=os.getenv("TW_ACCESS_SECRET")
)

# 🔹 Subreddit rotation
subs = ["funny", "memes", "aww", "nextfuckinglevel", "dankvideos"]
today_index = datetime.utcnow().day % len(subs)
sub = subs[today_index]

# 🔹 Fetch top Reddit post (last 24h)
url = f"https://www.reddit.com/r/{sub}/top.json?t=day&limit=5"
headers = {"User-agent": "reddit-twitter-bot"}
res = requests.get(url, headers=headers).json()

post = None
for child in res["data"]["children"]:
    data = child["data"]
    if "media" in data and data["media"] and "reddit_video" in str(data["media"]):
        post = data
        break

if not post:
    print("No video found.")
    exit()

video_url = post["media"]["reddit_video"]["fallback_url"]
title = post["title"]

# 🔹 Download video
video_file = "video.mp4"
with open(video_file, "wb") as f:
    f.write(requests.get(video_url).content)

# 🔹 Upload video to Twitter
auth = tweepy.OAuth1UserHandler(
    os.getenv("TW_CONSUMER_KEY"),
    os.getenv("TW_CONSUMER_SECRET"),
    os.getenv("TW_ACCESS_TOKEN"),
    os.getenv("TW_ACCESS_SECRET")
)
api = tweepy.API(auth)

media = api.media_upload(video_file)
api.update_status(status=f"From r/{sub}: {title}", media_ids=[media.media_id])

print(f"✅ Posted video from r/{sub}")
