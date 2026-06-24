import requests

public_id = "alex-perez-nyc"  # test with this first

cookies = {
    "li_at": "AQEFAHQBAAAAABlDiswAAAGaSAczQAAAAZ8Es71gTQAAF3VybjpsaTptZW1iZXI6ODc1NzkyMzkzDC_-MrFunjuHzzK66nAJ7Wg2B1atCk2qoB6XoD5Obgp4dEyk7xaHYqryYgn19JozXF9BSn3b5ZV5VXlcPQmoQ7U05Y954TmIF3p7xJi1hz1WMgxJGgTy19nrxQMVC7EL1FfWdQb7RXmgh-vucNOsJnEf1BLT53dMd_UCv09dwv4yYJCMtk6kj9kpwkl-_fYFqMIhzw",       # get a new one — old one is compromised
    "JSESSIONID": "ajax:1141711291238314636",
}

csrf_token = cookies["JSESSIONID"].strip('"')

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/vnd.linkedin.normalized+json+2.1",
    "x-li-lang": "en_US",
    "x-restli-protocol-version": "2.0.0",
    "csrf-token": csrf_token,
}

# ✅ NEW WORKING ENDPOINT
url = (
    f"https://www.linkedin.com/voyager/api/identity/dash/profiles"
    f"?q=memberIdentity"
    f"&memberIdentity={public_id}"
    f"&decorationId=com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-93"
)

response = requests.get(url, headers=headers, cookies=cookies)
data = response.json()

# Parse the flat `included` array — Voyager doesn't return a clean nested object
profile_obj = None
for item in data.get("included", []):
    if item.get("firstName") and "fsd_profile:" in item.get("entityUrn", ""):
        profile_obj = item
        break

if profile_obj:
    urn = profile_obj.get("entityUrn", "")
    # Extract the ACoAAA... part from urn:li:fsd_profile:ACoAAA...
    urn_id = urn.split("fsd_profile:")[-1] if "fsd_profile:" in urn else urn

    print("URN:", urn)
    print("URN ID:", urn_id)
    print("Name:", profile_obj.get("firstName"), profile_obj.get("lastName"))
    print("Headline:", profile_obj.get("headline"))
    print("Summary:", profile_obj.get("summary", "")[:200])
else:
    print("Status:", response.status_code)
    print("Raw:", data)