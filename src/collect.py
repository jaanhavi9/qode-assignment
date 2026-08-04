from __future__ import annotations

import json
import logging
import random
import re
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from selenium import webdriver
from selenium.common.exceptions import (
    InvalidSessionIdException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

logger = logging.getLogger(__name__)

MENTION_RE = re.compile(r"@([A-Za-z0-9_]{1,15})")
HASHTAG_RE = re.compile(r"#(\w+)", re.UNICODE)


def _parse_count(raw: str | None) -> int:
    if not raw:
        return 0
    text = raw.strip().replace(",", "")
    match = re.search(r"([\d.]+)\s*([KkMm])?", text)
    if not match:
        return 0
    value = float(match.group(1))
    suffix = (match.group(2) or "").lower()
    if suffix == "k":
        value *= 1_000
    elif suffix == "m":
        value *= 1_000_000
    return int(value)


def _build_driver(scrape_cfg: dict[str, Any], root: Path) -> webdriver.Chrome:
    options = Options()
    options.binary_location = scrape_cfg["chrome_binary"]
    # Manual login requires a visible browser.
    if scrape_cfg["headless"] and scrape_cfg.get("login_mode") != "manual":
        options.add_argument("--headless=new")
    options.add_argument(f"--window-size={scrape_cfg['window_width']},{scrape_cfg['window_height']}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    )

    profile_dir = root / scrape_cfg.get("chrome_user_data_dir", "data/chrome_profile")
    profile_dir.mkdir(parents=True, exist_ok=True)
    options.add_argument(f"--user-data-dir={profile_dir}")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(scrape_cfg["page_load_timeout_sec"])
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )
    return driver


def _wait(driver: webdriver.Chrome, seconds: int) -> WebDriverWait:
    return WebDriverWait(driver, seconds)


def _dump_debug(driver: webdriver.Chrome, root: Path, label: str) -> None:
    debug_dir = root / "logs" / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    screenshot = debug_dir / f"{label}_{stamp}.png"
    html_path = debug_dir / f"{label}_{stamp}.html"
    try:
        driver.save_screenshot(str(screenshot))
        html_path.write_text(driver.page_source, encoding="utf-8")
        logger.error("Saved debug artifacts: %s , %s", screenshot, html_path)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to dump debug artifacts: %s", exc)


def _find_first(driver: webdriver.Chrome, selectors: list[str], timeout: int):
    end = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < end:
        for selector in selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for element in elements:
                    if element.is_displayed():
                        return element
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        time.sleep(0.5)
    if last_error:
        raise TimeoutException(str(last_error))
    raise TimeoutException(f"No visible element for selectors={selectors}")


def _dismiss_overlays(driver: webdriver.Chrome) -> None:
    candidates = [
        '[data-testid="cookie-policy-manage-dialog"] button',
        '[role="button"][data-testid="PrimaryColumn"]',
        "//span[text()='Accept all cookies']/ancestor::button",
        "//span[text()='Accept']/ancestor::button",
        "//span[text()='Refuse non-essential cookies']/ancestor::button",
    ]
    for selector in candidates:
        try:
            if selector.startswith("//"):
                elements = driver.find_elements(By.XPATH, selector)
            else:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
            for element in elements:
                if element.is_displayed():
                    element.click()
                    time.sleep(1)
        except Exception:  # noqa: BLE001
            continue


LOGGED_IN_SELECTOR = (
    '[data-testid="AppTabBar_Home_Link"], a[href="/home"], '
    '[data-testid="SideNav_AccountSwitcher_Button"], [data-testid="AppTabBar_Profile_Link"]'
)


def _is_logged_in(driver: webdriver.Chrome) -> bool:
    try:
        return bool(driver.find_elements(By.CSS_SELECTOR, LOGGED_IN_SELECTOR))
    except Exception:  # noqa: BLE001
        return False


def _manual_login(driver: webdriver.Chrome, config: dict[str, Any]) -> None:
    scrape_cfg = config["scrape"]
    root = Path(config["root"])
    timeout = int(scrape_cfg.get("manual_login_timeout_sec", 300))

    driver.get("https://x.com/home")
    time.sleep(3)
    _dismiss_overlays(driver)

    if _is_logged_in(driver):
        logger.info("Existing Chrome profile session detected; skipping manual login")
        return

    driver.get(scrape_cfg["login_url"])
    time.sleep(2)
    _dismiss_overlays(driver)

    logger.info("=" * 72)
    logger.info("MANUAL LOGIN REQUIRED")
    logger.info("1. In the opened Chrome window, log in to X normally.")
    logger.info("2. Complete any CAPTCHA / email / phone checks if shown.")
    logger.info("3. Wait until your Home timeline is visible.")
    logger.info("Scraping starts automatically after login (timeout=%ss).", timeout)
    logger.info("=" * 72)

    wait = _wait(driver, timeout)
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, LOGGED_IN_SELECTOR)))
    except TimeoutException:
        _dump_debug(driver, root, "manual_login_timeout")
        raise TimeoutException(
            f"Manual login not detected within {timeout}s. "
            "Log in until the Home feed is visible, then re-run collect."
        )
    logger.info("Manual login detected")


def _auto_login(driver: webdriver.Chrome, config: dict[str, Any]) -> None:
    scrape_cfg = config["scrape"]
    creds = config["credentials"]
    root = Path(config["root"])
    wait = _wait(driver, scrape_cfg["explicit_wait_sec"])

    logger.info("Opening X login page")
    for url in (scrape_cfg["login_url"], "https://x.com/login", "https://twitter.com/i/flow/login"):
        driver.get(url)
        time.sleep(4)
        _dismiss_overlays(driver)
        try:
            username_input = _find_first(
                driver,
                [
                    'input[autocomplete="username"]',
                    'input[name="text"]',
                    'input[type="text"]',
                ],
                timeout=12,
            )
            break
        except TimeoutException:
            logger.warning("Username field not found at %s", url)
            _dump_debug(driver, root, "login_username_missing")
    else:
        raise TimeoutException("Unable to locate X username field after trying login URLs")

    username_input.clear()
    username_input.send_keys(creds["username"])
    username_input.send_keys(Keys.ENTER)
    time.sleep(3)

    try:
        challenge = _find_first(
            driver,
            [
                'input[data-testid="ocfEnterTextTextInput"]',
                'input[name="text"]',
            ],
            timeout=5,
        )
        if not driver.find_elements(By.CSS_SELECTOR, 'input[name="password"]'):
            challenge.clear()
            challenge.send_keys(creds["username"])
            challenge.send_keys(Keys.ENTER)
            time.sleep(3)
    except TimeoutException:
        pass

    try:
        password_input = _find_first(
            driver,
            [
                'input[name="password"]',
                'input[type="password"]',
                'input[autocomplete="current-password"]',
            ],
            timeout=scrape_cfg["explicit_wait_sec"],
        )
    except TimeoutException:
        _dump_debug(driver, root, "login_password_missing")
        raise

    password_input.clear()
    password_input.send_keys(creds["password"])
    password_input.send_keys(Keys.ENTER)

    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, LOGGED_IN_SELECTOR)))
    except TimeoutException:
        _dump_debug(driver, root, "login_home_missing")
        raise
    logger.info("Login successful")


def _cookie_login(driver: webdriver.Chrome, config: dict[str, Any]) -> None:
    creds = config["credentials"]
    root = Path(config["root"])

    driver.get("https://x.com")
    time.sleep(2)

    driver.delete_all_cookies()
    for name, value in (
        ("auth_token", creds["auth_token"]),
        ("ct0", creds["ct0"]),
    ):
        driver.add_cookie(
            {
                "name": name,
                "value": value,
                "domain": ".x.com",
                "path": "/",
                "secure": True,
                "httpOnly": name == "auth_token",
            }
        )

    driver.get("https://x.com/home")
    time.sleep(4)
    _dismiss_overlays(driver)

    if not _is_logged_in(driver):
        _dump_debug(driver, root, "cookie_login_failed")
        raise TimeoutException(
            "Cookie login failed. Re-copy auth_token and ct0 from Chrome DevTools while logged in on x.com."
        )
    logger.info("Cookie login successful")


def _login(driver: webdriver.Chrome, config: dict[str, Any]) -> None:
    mode = config["scrape"].get("login_mode", "manual")
    if mode == "cookies":
        _cookie_login(driver, config)
    elif mode == "manual":
        _manual_login(driver, config)
    else:
        _auto_login(driver, config)


def _search_url(query: str, base_url: str, tab: str) -> str:
    return f"{base_url}?q={quote(query)}&src=typed_query&f={tab}"


def _load_existing_ids(raw_dir: Path) -> OrderedDict[str, None]:
    seen: OrderedDict[str, None] = OrderedDict()
    for path in sorted(raw_dir.glob("tweets_*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    tweet_id = str(json.loads(line).get("tweet_id", ""))
                except json.JSONDecodeError:
                    continue
                if tweet_id:
                    seen[tweet_id] = None
    return seen


def _build_queries(hashtags: list[str], since_date: str, combined: bool) -> list[tuple[str, str]]:
    queries: list[tuple[str, str]] = []
    if combined and len(hashtags) > 1:
        joined = " OR ".join(f"#{tag}" for tag in hashtags)
        queries.append(("mixed", f"({joined}) since:{since_date}"))
    for tag in hashtags:
        queries.append((tag, f"#{tag} since:{since_date}"))
        queries.append((tag, f"#{tag} lang:en since:{since_date}"))
    return queries


def _extract_tweet(article, query_tag: str, scraped_at: str) -> dict[str, Any] | None:
    try:
        time_el = article.find_element(By.CSS_SELECTOR, "time")
        timestamp = time_el.get_attribute("datetime")
        permalink = time_el.find_element(By.XPATH, "./ancestor::a[1]").get_attribute("href")
    except (NoSuchElementException, StaleElementReferenceException):
        return None

    if not permalink or "/status/" not in permalink:
        return None
    tweet_id = permalink.rstrip("/").split("/")[-1]

    try:
        user_block = article.find_element(By.CSS_SELECTOR, '[data-testid="User-Name"]')
        user_text = user_block.text.split("\n")
        username = next((part[1:] for part in user_text if part.startswith("@")), user_text[0] if user_text else "")
    except (NoSuchElementException, StaleElementReferenceException):
        username = ""

    try:
        content = article.find_element(By.CSS_SELECTOR, '[data-testid="tweetText"]').text
    except NoSuchElementException:
        content = ""

    metrics = {"replies": 0, "retweets": 0, "likes": 0, "views": 0}
    mapping = {
        "reply": "replies",
        "retweet": "retweets",
        "like": "likes",
    }
    for test_id, key in mapping.items():
        try:
            el = article.find_element(By.CSS_SELECTOR, f'[data-testid="{test_id}"]')
            metrics[key] = _parse_count(el.get_attribute("aria-label") or el.text)
        except (NoSuchElementException, StaleElementReferenceException):
            pass

    try:
        analytics = article.find_element(By.CSS_SELECTOR, 'a[href*="/analytics"]')
        metrics["views"] = _parse_count(analytics.get_attribute("aria-label") or analytics.text)
    except (NoSuchElementException, StaleElementReferenceException):
        pass

    mentions = MENTION_RE.findall(content)
    hashtags = [tag.lower() for tag in HASHTAG_RE.findall(content)]

    return {
        "tweet_id": tweet_id,
        "username": username,
        "timestamp": timestamp,
        "content": content,
        "replies": metrics["replies"],
        "retweets": metrics["retweets"],
        "likes": metrics["likes"],
        "views": metrics["views"],
        "mentions": mentions,
        "hashtags": hashtags,
        "query_tag": query_tag,
        "permalink": permalink,
        "scraped_at": scraped_at,
    }


def _append_checkpoint(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def collect_tweets(config: dict[str, Any]) -> Path:
    scrape_cfg = config["scrape"]
    root = Path(config["root"])
    raw_dir = root / config["paths"]["raw_dir"]
    raw_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = raw_dir / f"tweets_{stamp}.jsonl"

    since_dt = datetime.now(timezone.utc) - timedelta(hours=config["lookback_hours"])
    since_date = since_dt.strftime("%Y-%m-%d")
    target = int(config["target_tweet_count"])
    hashtags = config["hashtags"]
    tabs = scrape_cfg.get("search_tabs", ["live"])
    queries = _build_queries(hashtags, since_date, bool(scrape_cfg.get("use_combined_query", False)))

    seen = _load_existing_ids(raw_dir)
    logger.info("Resuming with %s tweets already collected", len(seen))
    buffer: list[dict[str, Any]] = []
    driver: webdriver.Chrome | None = None

    def ensure_driver() -> webdriver.Chrome:
        nonlocal driver
        if driver is not None:
            try:
                _ = driver.current_url
                return driver
            except WebDriverException:
                logger.warning("Browser session lost; restarting Chrome")
                try:
                    driver.quit()
                except Exception:
                    pass
                driver = None
        driver = _build_driver(scrape_cfg, root)
        _login(driver, config)
        return driver

    try:
        ensure_driver()

        for tag, query in queries:
            if len(seen) >= target:
                break
            for tab in tabs:
                if len(seen) >= target:
                    break

                url = _search_url(query, scrape_cfg["search_base_url"], tab)
                logger.info("Scraping tag=%s tab=%s (%s)", tag, tab, url)

                try:
                    active = ensure_driver()
                    active.get(url)
                    time.sleep(scrape_cfg["scroll_pause_sec"])
                    wait = _wait(active, scrape_cfg["explicit_wait_sec"])
                    wait.until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, 'article[data-testid="tweet"]'))
                    )
                except TimeoutException:
                    logger.warning("No tweets rendered for tag=%s tab=%s", tag, tab)
                    continue
                except (InvalidSessionIdException, WebDriverException) as exc:
                    logger.warning("Navigation failed (%s); recovering session", exc.__class__.__name__)
                    ensure_driver()
                    continue

                idle_scrolls = 0
                last_count = len(seen)

                while len(seen) < target and idle_scrolls < scrape_cfg["max_idle_scrolls"]:
                    try:
                        active = ensure_driver()
                        articles = active.find_elements(By.CSS_SELECTOR, 'article[data-testid="tweet"]')
                        scraped_at = datetime.now(timezone.utc).isoformat()

                        for article in articles:
                            if len(seen) >= target:
                                break
                            try:
                                row = _extract_tweet(article, tag, scraped_at)
                            except StaleElementReferenceException:
                                continue
                            if row is None or row["tweet_id"] in seen:
                                continue
                            seen[row["tweet_id"]] = None
                            buffer.append(row)

                            if len(buffer) >= scrape_cfg["checkpoint_every"]:
                                _append_checkpoint(out_path, buffer)
                                logger.info(
                                    "Checkpointed %s tweets (unique=%s)", len(buffer), len(seen)
                                )
                                buffer.clear()

                        active.execute_script("window.scrollBy(0, document.body.scrollHeight);")
                        pause = scrape_cfg["scroll_pause_sec"] + random.uniform(
                            0, scrape_cfg["scroll_pause_jitter_sec"]
                        )
                        time.sleep(pause)
                    except (InvalidSessionIdException, WebDriverException) as exc:
                        logger.warning("Scroll loop interrupted (%s); recovering", exc.__class__.__name__)
                        _append_checkpoint(out_path, buffer)
                        buffer.clear()
                        ensure_driver()
                        break

                    if len(seen) == last_count:
                        idle_scrolls += 1
                    else:
                        idle_scrolls = 0
                        last_count = len(seen)

                    logger.info(
                        "tag=%s tab=%s progress: unique=%s idle_scrolls=%s",
                        tag,
                        tab,
                        len(seen),
                        idle_scrolls,
                    )

        _append_checkpoint(out_path, buffer)
        buffer.clear()
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass

    logger.info("Collection complete: %s unique tweets -> %s", len(seen), out_path)
    if len(seen) < target:
        logger.warning(
            "Collected %s tweets which is below target %s; rate limits or UI changes may apply",
            len(seen),
            target,
        )
    return out_path
