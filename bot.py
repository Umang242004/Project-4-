import os
import requests
import tweepy
import random, hashlib
from datetime import datetime

# 🔑 Twitter API Auth
client = tweepy.Client(
    consumer_key=os.getenv("TW_CONSUMER_KEY"),
    consumer_secret=os.getenv("TW_CONSUMER_SECRET"),
    access_token=os.getenv("TW_ACCESS_TOKEN"),
    access_token_secret=os.getenv("TW_ACCESS_SECRET")
)

# 🔹 Subreddit pool
subs = ["interestingasfuck", "Damnthatsinteresting", "toptalent", "nextfuckinglevel", "BeAmazed"]

# Generate deterministic shuffle for today
today_str = datetime.utcnow().strftime("%Y-%m-%d")
seed = int(hashlib.sha256(today_str.encode()).hexdigest(), 16)
daily_subs = subs.copy()
random.Random(seed).shuffle(daily_subs)

# Map posting hours to slots
slot_map = {9: 0, 12: 1, 15: 2, 18: 3, 21: 4}
hour = datetime.utcnow().hour
slot = slot_map.get(hour, 0)  # default = 0 if run at wrong time

sub = daily_subs[slot % len(daily_subs)]
print(f"🎯 Selected subreddit: {sub}")

# 🔹 Fetch top Reddit post (last 24h)
url = f"https://www.reddit.com/r/{sub}/top.json?t=day&limit=10"
headers = {"User-agent": "reddit-twitter-bot"}
res = requests.get(url, headers=headers).json()

post = None
for child in res["data"]["children"]:
    data = child["data"]
    if "media" in data and data["media"] and "reddit_video" in str(data["media"]):
        post = data
        break

if not post:
    print(f"❌ No video found in r/{sub}")
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
