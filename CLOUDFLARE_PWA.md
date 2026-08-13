# Cloudflare-Konfiguration für v40

## Ziel

- `betreuung.DEINE-DOMAIN.TLD`: bestehende Flask-App hinter Cloudflare Access
- Browserzugang: OTP / normale Allow-Policy
- PWA-Zugang: Service Auth / Service Token
- `betreuung2.DEINE-DOMAIN.TLD`: statisches GitHub-Pages-Frontend

## Access

Auf der Access Application für `betreuung.DEINE-DOMAIN.TLD` beide Zugangsarten zulassen:

1. normale **Allow**-Policy für deinen OTP-Benutzer
2. **Service Auth**-Policy mit den Service Tokens der iPhones

Am besten pro Gerät ein separates Service Token erstellen.

## CORS / Preflight

Die PWA läuft auf einem anderen Origin. Browser senden deshalb vor Requests mit den Cloudflare-Service-Token-Headern einen anonymen `OPTIONS`-Preflight. In der Access Application die CORS-Preflight-Behandlung am Cloudflare Edge aktivieren und nicht den Origin pauschal öffentlich machen.

Erlaubte Werte:

```text
Access-Control-Allow-Origin: https://betreuung2.DEINE-DOMAIN.TLD
Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS
Access-Control-Allow-Headers: Content-Type, CF-Access-Client-ID, CF-Access-Client-Secret
Access-Control-Expose-Headers: Content-Disposition, Content-Type
Access-Control-Max-Age: 86400
```

Im Container muss derselbe PWA-Origin stehen:

```text
PWA_ALLOWED_ORIGIN=https://betreuung2.DEINE-DOMAIN.TLD
```

## iCal-Abos

`/calendar.ics` bleibt ein Sonderfall: Apple Kalender kann das Cloudflare-Service-Token nicht als Custom Header senden. Falls Kalender-Abos verwendet werden, den bestehenden gezielten Cloudflare-Bypass nur für `/calendar.ics` beibehalten; der lange iCal-Token der App bleibt die Absicherung dieses Endpunkts.
