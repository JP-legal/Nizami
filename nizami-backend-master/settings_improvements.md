# settings.py — Improvements To Fix

## 1. Security: `ALLOWED_HOSTS = ['*']`
**Line 54–56**
Allows any host. In production this should be a list of actual domains (e.g. `api.nizami.ai`).
```python
ALLOWED_HOSTS = ['api.nizami.ai', 'api.app.nizami.ai']
```

---

## 2. Security: `SECRET_KEY` has an insecure default
**Line 47**
The fallback `"django-insecure-test-key-change-in-production"` could accidentally be used in production if the env var is missing. Raise an error instead when not testing:
```python
SECRET_KEY = env("SECRET_KEY") if not TESTING else "django-insecure-test-key-for-testing-only"
```

---

## 3. Security: All password validators are commented out
**Lines 188–200**
`AUTH_PASSWORD_VALIDATORS` is empty — no password strength rules are enforced. Uncomment at minimum `MinimumLengthValidator` and `NumericPasswordValidator`.

---

## 4. Security: JWT `ACCESS_TOKEN_LIFETIME` is 365 days
**Line 276**
A 1-year access token is a major security risk. If a token is leaked it stays valid for a year. Recommended: 15–60 minutes for access tokens, and use refresh tokens.
```python
'ACCESS_TOKEN_LIFETIME': timedelta(minutes=30),
'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
'ROTATE_REFRESH_TOKENS': True,
'BLACKLIST_AFTER_ROTATION': True,
```

---

## 5. `embeddings` and `vectorstore` initialized at module load time
**Lines 334–345**
Heavy objects (OpenAI client, PGVector connection) are created when Django starts, even in management commands and migrations. This also silently swallows all exceptions. Move initialization to a lazy singleton or an `AppConfig.ready()` hook, and log the exception rather than silencing it.

---

## 6. `logs` database is the same connection as `default`
**Lines 173–181**
The `logs` DB uses the exact same credentials/host/name as `default`. Either point it to a separate database or remove it to avoid confusion.

---

## 7. `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS` should come from env in production
**Lines 58–73, 347–360**
These lists are hardcoded. Prefer reading them from an env var (comma-separated) so deployments don't require code changes:
```python
CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=[...])
```

---

## 8. `TESTING` detection is fragile
**Lines 27–32**
Using `sys.argv` to detect test mode can mis-fire (e.g. a script named `test`). The standard pattern is a dedicated `TEST_SETTINGS` env var or a separate `settings_test.py` file that imports from base settings.

---

## 9. `# MAIL_MAILER=smtp` stale comment
**Line 300**
Leftover comment from a Laravel/PHP project. Remove it.

---

## 10. `RAG_SOURCE = 'old'` default
**Line 372**
A magic string default of `'old'` is unclear and error-prone. Consider using an explicit `Enum` or at least a named constant, and document the two valid values in a comment above.
