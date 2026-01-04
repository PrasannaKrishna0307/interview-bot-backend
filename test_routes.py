import requests

try:
    print("Testing API connectivity...")
    # Test Root (usually 404 but tells us server is up)
    try:
        r = requests.get('http://localhost:8000/')
        print(f"Root: {r.status_code}")
    except Exception as e:
        print(f"Root failed: {e}")

    # Test Register POST (should be 422 Unprocessable Entity if body missing, or 200/400 if logic runs, NOT 404)
    # If 404, it means route not found.
    r = requests.post('http://localhost:8000/api/auth/register', json={
        "email": "test@example.com", 
        "password": "pass", 
        "name": "test"
    })
    print(f"POST /api/auth/register: {r.status_code}")
    print(r.text)

except Exception as e:
    print(f"Error: {e}")
