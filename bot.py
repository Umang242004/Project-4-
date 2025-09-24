import os
import requests
import tweepy
import random
import hashlib
import time
import logging
import subprocess
import shutil
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------------------------
# CONFIG: edit these as you like
# ---------------------------
# subreddit pool (add/remove subreddits here)
SUBS = ["interestingasfuck", "Damnthatsinteresting", "toptalent", "nextfuckinglevel", "BeAmazed"]

# fallback subs if none of today's subs have videos
FALLBACK_SUBS = ["videos", "funny", "gifs"]

# Reddit fetch limit per subreddit (more = more API calls)
REDDIT_FETCH_LIMIT = 15

# Local filenames
RAW_VIDEO = "raw_video.mp4"
UPLOAD_VIDEO = "upload_video.mp4"

# User-agent for Reddit requests — replace "your_reddit_username" with your reddit username
REDDIT_UA = "reddit-twitter-bot/0.1 (by u/your_reddit_username)"
# ---------------------------


def get_daily_subs(subs):
    """Deterministic shuffle per UTC date and return at least 5 items (if available)."""
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    seed = int(hashlib.sha256(today_str.encode()).hexdigest(), 16)
    subs_copy = subs.copy()
    random.Random(seed).shuffle(subs_copy)
    # ensure at least up to 5 (or the length of list if smaller)
    return subs_copy[:5] if len(subs_copy) >= 5 else subs_copy


def get_slot_map():
    """Map UTC hour to slot index"""
    return {9: 0, 12: 1, 15: 2, 18: 3, 21: 4}


def fetch_reddit_top_video(sub, limit=REDDIT_FETCH_LIMIT):
    """Return dict {'title', 'video_url'} or None if not found."""
    url = f"https://www.reddit.com/r/{sub}/top.json?t=day&limit={limit}"
    headers = {"User-Agent": REDDIT_UA}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logging.warning("Failed to fetch r/%s: %s", sub, e)
        return None

    for child in data.get("data", {}).get("children", []):
        d = child.get("data", {})
        # Best: reddit-hosted video
        if d.get("is_video") and d.get("media") and isinstance(d["media"], dict):
            rv = d["media"].get("reddit_video") or (d.get("secure_media") and d["secure_media"].get("reddit_video"))
            if rv and rv.get("fallback_url"):
                return {"title": d.get("title", ""), "video_url": rv["fallback_url"]}

        # direct mp4 link
        url_dest = d.get("url_overridden_by_dest") or d.get("url")
        if url_dest and url_dest.endswith(".mp4"):
            return {"title": d.get("title", ""), "video_url": url_dest}

        # sometimes v.redd.it links show up in url; try them
        if url_dest and "v.redd.it" in url_dest:
            return {"title": d.get("title", ""), "video_url": url_dest}

    return None


def download_stream(url, filename):
    """Stream-download file to filename. Returns True on success."""
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
    """Transcode to a Twitter-friendly mp4 using ffmpeg if available. Returns True on success or if skipped."""
    if shutil.which("ffmpeg") is None:
        logging.info("ffmpeg not found — skipping transcode (upload raw file).")
        # copy raw to outfile for consistent naming if possible
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
        logging.info("Running ffmpeg to transcode...")
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except Exception as e:
        logging.error("ffmpeg failed: %s", e)
        return False


def upload_to_twitter(api, filename, caption):
    """Upload media and post tweet. Returns True on success."""
    try:
        # Tweepy will do chunked upload if needed (API.media_upload)
        media = api.media_upload(filename)
        api.update_status(status=caption, media_ids=[media.media_id])
        return True
    except Exception as e:
        logging.error("Twitter upload/post failed: %s", e)
        return False


def main():
    # Validate env vars
    required = ["TW_CONSUMER_KEY", "TW_CONSUMER_SECRET", "TW_ACCESS_TOKEN", "TW_ACCESS_SECRET"]
    for k in required:
        if not os.getenv(k):
            logging.error("Missing environment variable: %s", k)
            return

    # Create Tweepy API (OAuth1) for media upload & posting
    auth = tweepy.OAuth1UserHandler(
        os.getenv("TW_CONSUMER_KEY"),
        os.getenv("TW_CONSUMER_SECRET"),
        os.getenv("TW_ACCESS_TOKEN"),
        os.getenv("TW_ACCESS_SECRET"),
    )
    api = tweepy.API(auth, wait_on_rate_limit=True)

    # daily subs and slot logic
    daily = get_daily_subs(SUBS)
    slot_map = get_slot_map()
    current_hour = datetime.utcnow().hour
    slot = slot_map.get(current_hour, 0)
    # create order starting from this slot so each scheduled run picks a unique sub
    ordered = daily[slot:] + daily[:slot]
    logging.info("Daily subs (order): %s", ordered)

    # search each sub in ordered list for a valid video
    found = None
    selected_sub = None
    for s in ordered:
        logging.info("Searching r/%s for top video...", s)
        found = fetch_reddit_top_video(s)
        if found:
            selected_sub = s
            break
        time.sleep(2)  # be polite, avoid hammering Reddit

    # if still not found, try fallback subs
    if not found:
        logging.info("Trying fallback subreddits...")
        for s in FALLBACK_SUBS:
            logging.info("Searching fallback r/%s...", s)
            found = fetch_reddit_top_video(s)
            if found:
                selected_sub = s
                break
            time.sleep(2)

    if not found:
        logging.error("No video found in today's subs or fallback subs. Exiting.")
        return

    logging.info("Found video in r/%s: %s", selected_sub, found["title"])
    video_url = found["video_url"]
    title = found["title"]

    # download raw video
    if not download_stream(video_url, RAW_VIDEO):
        logging.error("Download failed — abort.")
        return

    # transcode / prepare upload file
    if not ffmpeg_transcode(RAW_VIDEO, UPLOAD_VIDEO):
        logging.error("Transcode/prep failed — abort.")
        return

    caption = f"From r/{selected_sub}: {title}"
    logging.info("Uploading to Twitter with caption: %s", caption[:120])

    success = upload_to_twitter(api, UPLOAD_VIDEO, caption)

    # cleanup
    for f in [RAW_VIDEO, UPLOAD_VIDEO]:
        try:
            if os.path.exists(f):
                os.remove(f)
        except Exception:
            pass

    if success:
        logging.info("✅ Posted video from r/%s", selected_sub)
    else:
        logging.error("Posting failed.")


if __name__ == "__main__":
    main()
