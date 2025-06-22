"""Lightweight Finviz scraper.

For simplicity, we scrape the public Finviz page. If you have a paid
Finviz API plan or want to switch to Benzinga, simply replace
`fetch_top_n` with a new implementation while keeping the signature.

The scraper is deliberately minimal-footprint: **httpx + BeautifulSoup**.
No heavy-weight Selenium or browser automation is required.
"""

import json
import logging
import re
from typing import List, Set, Dict, Any, Optional # Added Optional
from urllib.parse import urlparse, parse_qs

import httpx # Keep for type hints if any, but direct calls will be removed
from bs4 import BeautifulSoup

from config import settings, FINVIZ_CONFIG_FILE, DEFAULT_TICKER_REFRESH_SEC # Added FINVIZ_CONFIG_FILE and DEFAULT_TICKER_REFRESH_SEC

_logger = logging.getLogger("finviz_parser")

# --- Configuration Management ---
def load_finviz_config() -> Dict[str, Any]:
    """Loads Finviz configuration (URL, TOP_N, refresh interval) from a JSON file."""
    try:
        with open(FINVIZ_CONFIG_FILE, "r") as f:
            config_data = json.load(f)
            # Ensure essential keys are present, provide defaults if not
            if "finviz_url" not in config_data:
                _logger.warning(f"'finviz_url' not found in {FINVIZ_CONFIG_FILE}, this might cause issues.")
                config_data["finviz_url"] = None # Or a safe default
            if "top_n" not in config_data:
                _logger.warning(f"'top_n' not found in {FINVIZ_CONFIG_FILE}, this might cause issues.")
                config_data["top_n"] = 0 # Or a safe default
            config_data.setdefault("refresh_interval_sec", DEFAULT_TICKER_REFRESH_SEC)
            return config_data
    except FileNotFoundError:
        _logger.warning(f"{FINVIZ_CONFIG_FILE} not found. Returning empty config or defaults.")
        # It's critical that the application can handle a missing config file,
        # especially on first run or if FinvizEngine needs to create it.
        # The engine should ideally create a default one if it doesn't exist.
        # For now, return a structure that won't break the caller immediately.
        return {
            "finviz_url": None, # Indicate that it needs to be set
            "top_n": 0,         # Indicate that it needs to be set
            "refresh_interval_sec": DEFAULT_TICKER_REFRESH_SEC
        }
    except json.JSONDecodeError:
        _logger.error(f"Error decoding JSON from {FINVIZ_CONFIG_FILE}. Please check its format.")
        raise # Re-raise as this is a critical error for config loading

def persist_finviz_config(url: str, top_n: int, refresh_sec: Optional[int] = None) -> None:
    """Persists Finviz configuration to a JSON file."""
    if refresh_sec is None:
        # Try to load existing to preserve refresh interval if not provided
        try:
            current_config = load_finviz_config()
            refresh_sec = current_config.get("refresh_interval_sec", DEFAULT_TICKER_REFRESH_SEC)
        except (FileNotFoundError, json.JSONDecodeError):
            refresh_sec = DEFAULT_TICKER_REFRESH_SEC # Fallback if load fails

    config_data = {
        "finviz_url": url,
        "top_n": top_n,
        "refresh_interval_sec": refresh_sec
    }
    try:
        with open(FINVIZ_CONFIG_FILE, "w") as f:
            json.dump(config_data, f, indent=4)
        _logger.info(f"Finviz config persisted to {FINVIZ_CONFIG_FILE}: URL={url}, TopN={top_n}, Refresh={refresh_sec}s")
    except IOError as e:
        _logger.error(f"Error writing Finviz config to {FINVIZ_CONFIG_FILE}: {e}")
        raise # Re-raise to signal failure to persist

def persist_finviz_config_from_dict(config_dict: Dict[str, Any]) -> None:
    """
    Persists Finviz configuration to a JSON file from a dictionary.
    Ensures 'finviz_url', 'top_n', and 'refresh_interval_sec' are present.
    """
    # Validate essential keys
    if not all(key in config_dict for key in ["finviz_url", "top_n", "refresh_interval_sec"]):
        _logger.error(f"Attempted to persist incomplete config: {config_dict}. Missing essential keys.")
        raise ValueError("Config dictionary must contain 'finviz_url', 'top_n', and 'refresh_interval_sec'.")

    try:
        with open(FINVIZ_CONFIG_FILE, "w") as f:
            json.dump(config_dict, f, indent=4)
        _logger.info(f"Finviz config persisted from dict to {FINVIZ_CONFIG_FILE}: {config_dict}")
    except IOError as e:
        _logger.error(f"Error writing Finviz config to {FINVIZ_CONFIG_FILE} from dict: {e}")
        raise


# --- HTML Parsing Logic (kept from original, ensure it's robust) ---
def parse_tickers_from_html(html_content: str) -> List[str]:
    """
    Parses HTML content from a Finviz screener page and extracts stock tickers.
    This function should be robust to minor changes in Finviz's HTML structure.
    """
    tickers: List[str] = []
    if not html_content:
        _logger.warning("Empty HTML content received for parsing tickers.")
        return tickers

    try:
        soup = BeautifulSoup(html_content, "html.parser")
        # Finviz screener tables usually have a specific structure.
        # Find all <a> tags that look like ticker links.
        # Common pattern: class="screener-link-primary" or inside <td class="screener-body-table-nw">
        # This selector might need adjustment if Finviz changes its layout.
        # A more robust way is to find the main results table and then iterate rows/cells.
        # Example: soup.find_all('a', class_='screener-link-primary')
        
        # Let's try a more specific approach targeting the table cells known to contain tickers.
        # The tickers are usually the first link within a row in the main data table.
        # The table often has an id like `screener-table` or is identifiable by its structure.
        
        # Looking for links with hrefs like "quote.ashx?t=TICKER"
        ticker_links = soup.find_all("a", href=re.compile(r"quote\.ashx\?t=([A-Z0-9.-]+)"))

        for link in ticker_links:
            # Ensure the link is likely a primary ticker link, not other links on the page.
            # Primary ticker links often have a specific class or are the main text of a cell.
            # Heuristic: If the link's text matches the ticker extracted from href, it's likely correct.
            href = link.get("href")
            if href:
                match = re.search(r"quote\.ashx\?t=([A-Z0-9.-]+)", href)
                if match:
                    ticker = match.group(1)
                    # Further check: Does the link text itself look like a ticker?
                    # Finviz ticker links usually have the ticker symbol as their text.
                    if link.string and link.string.strip() == ticker:
                        if ticker not in tickers: # Avoid duplicates from the same page
                            tickers.append(ticker)
                    # else:
                        # _logger.debug(f"Link text '{link.string}' does not match ticker '{ticker}' from href, possibly not a primary ticker link.")
        
        if not tickers:
            _logger.warning(f"No tickers extracted. Check HTML structure or parsing logic. HTML snippet (first 500 chars): {html_content[:500]}")

    except Exception as e:
        _logger.error(f"Error parsing HTML for tickers: {e}", exc_info=True)
        # It's important to return an empty list or raise, rather than returning partial/bad data.
        return [] # Return empty list on error

    _logger.debug(f"Parsed {len(tickers)} tickers from HTML page.")
    return tickers


# --- URL Normalization (can be kept if used by parser or moved to engine) ---
# This is also in finviz_engine.py. Consolidate if possible.
# For now, keep it here if finviz.py might be used independently for parsing tests.
def normalise_url(url: str, remove_pagination: bool = False) -> str:
    """
    Normalises a Finviz URL with comprehensive handling for both free and Elite accounts.
    
    This function combines the functionality of the previous normalise_url and 
    normalise_url_for_finviz functions to provide a single, consistent implementation.
    
    Args:
        url (str): The URL to normalize
        remove_pagination (bool): If True, removes 'r' parameter for pagination control
        
    Returns:
        str: The normalized URL
        
    Features:
        - Ensures HTTPS protocol
        - Handles elite subdomain based on FINVIZ_USE_ELITE setting
        - Optionally removes pagination parameters
        - Preserves essential query parameters
        - Validates Finviz domain
    """
    if not url:
        raise ValueError("URL cannot be empty for normalization.")
    
    # Import here to avoid circular imports
    from config import settings
    
    # Handle simple regex-based normalization (faster for basic cases)
    if not remove_pagination and ('?' not in url or 'r=' not in url):
        # Use the simpler regex approach for basic URL normalization
        if not settings.FINVIZ_USE_ELITE:
            # Remove elite subdomain if Elite is disabled
            url = re.sub(r"^https?://elite\.finviz\.com", "https://finviz.com", url, flags=re.I)
        else:
            # For Elite users, ensure we're using the elite subdomain
            url = re.sub(r"^https?://finviz\.com", "https://elite.finviz.com", url, flags=re.I)
            url = re.sub(r"^http://elite\.finviz\.com", "https://elite.finviz.com", url, flags=re.I)
        
        # Always ensure http -> https for finviz.com
        url = re.sub(r"^http://finviz\.com", "https://finviz.com", url, flags=re.I)
        return url
    
    # Use comprehensive URL parsing approach for complex cases
    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)

    # Ensure scheme is https
    scheme = "https"
    
    # Handle subdomain based on Elite setting
    if not settings.FINVIZ_USE_ELITE:
        # Remove 'elite.' subdomain if Elite is disabled
        netloc = parsed_url.netloc.replace("elite.finviz.com", "finviz.com")
    else:
        # Ensure elite subdomain if Elite is enabled
        if parsed_url.netloc == "finviz.com":
            netloc = "elite.finviz.com"
        elif parsed_url.netloc == "elite.finviz.com":
            netloc = "elite.finviz.com"
        else:
            netloc = parsed_url.netloc
    
    # Basic domain validation
    if not netloc.endswith("finviz.com"):
        _logger.warning(f"URL '{url}' does not appear to be a valid Finviz domain. Proceeding cautiously.")
    
    # Remove pagination parameter if requested
    if remove_pagination:
        query_params.pop('r', None)

    # Reconstruct the query string
    new_query_parts = []
    for k, v_list in query_params.items():
        for v_item in v_list:
            new_query_parts.append(f"{k}={v_item}")
    
    new_query_string = "&".join(new_query_parts)

    # Reconstruct the URL
    path = parsed_url.path if parsed_url.path else "/screener.ashx"
    
    final_url = f"{scheme}://{netloc}{path}"
    if new_query_string:
        final_url += f"?{new_query_string}"
        
    _logger.debug(f"Normalized URL: '{url}' -> '{final_url}'")
    return final_url


# Legacy function name for backward compatibility
def normalise_url_for_finviz(url: str) -> str:
    """
    Legacy function for backward compatibility.
    Use normalise_url(url, remove_pagination=True) instead.
    """
    _logger.warning("normalise_url_for_finviz is deprecated. Use normalise_url(url, remove_pagination=True) instead.")
    return normalise_url(url, remove_pagination=True)

# Example usage (for testing the parser or config functions locally):
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    # Test config loading/saving
    # test_config = {"finviz_url": "https://finviz.com/screener.ashx?v=111&f=sh_curvol_o500000", "top_n": 50, "refresh_interval_sec": 60}
    # persist_finviz_config_from_dict(test_config)
    # loaded_cfg = load_finviz_config()
    # print(f"Loaded config: {loaded_cfg}")

    # Test HTML parsing (requires a sample HTML file or live fetch - not done here to keep it network-free)
    sample_html_file = "finviz_debug.html" # Create this file with sample Finviz screener HTML
    try:
        with open(sample_html_file, "r", encoding="utf-8") as f:
            sample_html = f.read()
        if sample_html:
            parsed_tickers = parse_tickers_from_html(sample_html)
            print(f"Parsed tickers from '{sample_html_file}': {parsed_tickers} (Count: {len(parsed_tickers)})")
        else:
            print(f"'{sample_html_file}' is empty or could not be read.")
    except FileNotFoundError:
        print(f"Sample HTML file '{sample_html_file}' not found. Skipping HTML parsing test.")
    except Exception as e:
        print(f"Error during local test of HTML parsing: {e}")

    # Test URL normalization
    # test_urls = [
    #     "http://finviz.com/screener.ashx?v=150&f=sh_avgvol_o500&r=21",
    #     "https://elite.finviz.com/screener.ashx?v=111&s=ta_p_channelup&o=-perf1w",
    #     "https://finviz.com/screener.ashx?v=111",
    #     "https://finviz.com/screener.ashx"
    # ]
    # for t_url in test_urls:
    #     try:
    #         print(f"Original: {t_url} -> Normalized: {normalise_url_for_finviz(t_url)}")
    #     except ValueError as ve:
    #         print(f"Error normalizing {t_url}: {ve}")
