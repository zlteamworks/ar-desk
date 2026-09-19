import json
import truststore
truststore.inject_into_ssl()   # <-- makes Python trust your Windows certificates

import requests

URL = "https://www.naukri.com/jobapi/v3/search"
HEADERS = {
    "authority": "www.naukri.com",
    "accept": "application/json",
    "accept-language": "en-US,en;q=0.9",
    "appid": "109",
    "systemid": "109",
    "referer": "https://www.naukri.com/",
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
}
PARAMS = {
    "noOfResults": 20,
    "urlType": "search_by_keyword",
    "searchType": "adv",
    "keyword": "accounts receivable",
    "pageNo": 1,
}

s = requests.Session()
try:
    s.get("https://www.naukri.com/", headers={"user-agent": HEADERS["user-agent"]}, timeout=20)
    print("Homepage visited. Got", len(s.cookies), "cookie(s).")
except Exception as e:
    print("Homepage visit failed:", e)

r = s.get(URL, headers=HEADERS, params=PARAMS, timeout=20)
print("STATUS CODE:", r.status_code)
print("CONTENT-TYPE:", r.headers.get("content-type"))
print("-" * 50)
try:
    data = r.json()
    print("TOP-LEVEL KEYS:", list(data.keys()))
    jd = data.get("jobDetails")
    if jd is None:
        print("No 'jobDetails' key. First 800 chars:")
        print(json.dumps(data)[:800])
    else:
        print("NUMBER OF JOBS:", len(jd))
        if jd:
            print("FIRST JOB TITLE:", jd[0].get("title"))
except Exception:
    print("Response was NOT JSON. First 800 chars:")
    print(r.text[:800])