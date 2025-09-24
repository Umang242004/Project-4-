import os
import tweepy
import random
import hashlib
import time
import logging
import subprocess
import shutil
from datetime import datetime, timezone
import praw

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------------------------
# CONFIG
# ---------------------------
SUBS = ["interestingasfuck", "Damnthatsinteresting", "toptalent", "nextfuckinglevel", "BeAmazed"]
FALLBACK_SUBS = ["videos", "funny", "gifs"]

RAW_VIDEO = "raw_video.mp4"
UPLOAD_VIDEO = "upload_video.mp4"

# ---------------------------
# Reddit Auth (read-only)
# ---------------------------
reddit = praw.Reddit(
    client_id=os.getenv("REDDIT_CLIENT_ID"),
    client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
    user_agent=os.getenv("REDDIT_USER_AGENT"),
)


def get_daily_subs(subs):
    """Deterministic shuffle per UTC date."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    seed = int(hashlib.sha256(today_str.encode()).hexdigest(), 16)
    subs_copy = subs.copy()
    random.Random(seed).shuffle(subs_copy)
    return subs_copy[:5] if len(subs_copy) >= 5 else subs_copy


def get_slot_map():
    """Map UTC hour to slot index"""
    return {9: 0, 12: 1, 15: 2, 18: 3, 21: 4}


def fetch_reddit_top_video(sub, limit=15):
    """Return dict {'title', 'video_url'} or None if not found."""
    try:
        subreddit = reddit.subreddit(sub)
        for post in subreddit.top(time_filter="day", limit=limit):
            if post.is_video and post.media and "reddit_video" in str(post.media):
                rv = post.media.get("reddit_video")
                if rv and rv.get("fallback_url"):
                    return {"title": post.title, "video_url": rv["fallback_url"]}
            if post.url.endswith(".mp4"):
                return {"title": post.title, "video_url": post.url}
            if "v.redd.it" in post.url:
                return {"title": post.title, "video_url": post.url}
        return None
    except Exception as e:
        logging.warning("Failed to fetch r/%s: %s", sub, e)
        return None


def download_stream(url, filename):
    import requests
    try:
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(filename, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        return True
    except Exception as e:
        logging.error("Download failed (%s): %s", url, e)
        return False


def ffmpeg_transcode(infile, outfile):
    if shutil.which("ffmpeg") is None:
        logging.info("ffmpeg not found — skipping transcode.")
        try:
            shutil.copyfile(infile, outfile)
            return True
        except Exception as e:
            logging.error("Could not copy file: %s", e)
            return False

    cmd = [
        "ffmpeg", "-y", "-i", infile,
        "-c:v", "libx264", "-preset", "fast",
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        outfile
    ]
    try:
        logging.info("Running ffmpeg...")
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except Exception as e:
        logging.error("ffmpeg failed: %s", e)
        return False


def upload_to_twitter(api, filename, caption):
    try:
        media = api.media_upload(filename)
        api.update_status(status=caption, media_ids=[media.media_id])
        return True
    except Exception as e:
        logging.error("Twitter upload failed: %s", e)
        return False


def main():
    required = ["TW_CONSUMER_KEY", "TW_CONSUMER_SECRET", "TW_ACCESS_TOKEN", "TW_ACCESS_SECRET"]
    for k in required:
        if not os.getenv(k):
            logging.error("Missing env var: %s", k)
            return

    auth = tweepy.OAuth1UserHandler(
        os.getenv("TW_CONSUMER_KEY"),
        os.getenv("TW_CONSUMER_SECRET"),
        os.getenv("TW_ACCESS_TOKEN"),
        os.getenv("TW_ACCESS_SECRET"),
    )
    api = tweepy.API(auth, wait_on_rate_limit=True)

    daily = get_daily_subs(SUBS)
    slot_map = get_slot_map()
    current_hour = datetime.now(timezone.utc).hour
    slot = slot_map.get(current_hour, 0)
    ordered = daily[slot:] + daily[:slot]
    logging.info("Daily subs (order): %s", ordered)

    found, selected_sub = None, None
    for s in ordered:
        logging.info("Searching r/%s...", s)
        found = fetch_reddit_top_video(s)
        if found:
            selected_sub = s
            break
        time.sleep(2)

    if not found:
        logging.info("Trying fallback subs...")
        for s in FALLBACK_SUBS:
            found = fetch_reddit_top_video(s)
            if found:
                selected_sub = s
                break
            time.sleep(2)

    if not found:
        logging.error("No video found.")
        return

    logging.info("Found in r/%s: %s", selected_sub, found["title"])
    if not download_stream(found["video_url"], RAW_VIDEO):
        return
    if not ffmpeg_transcode(RAW_VIDEO, UPLOAD_VIDEO):
        return

    caption = f"From r/{selected_sub}: {found['title']}"
    success = upload_to_twitter(api, UPLOAD_VIDEO, caption)

    for f in [RAW_VIDEO, UPLOAD_VIDEO]:
        if os.path.exists(f):
            os.remove(f)

    if success:
        logging.info("✅ Posted video from r/%s", selected_sub)


if __name__ == "__main__":
    main()
