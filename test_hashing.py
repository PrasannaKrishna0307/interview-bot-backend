from passlib.context import CryptContext
import sys

print(f"Python Version: {sys.version}")

try:
    # Initialize context exactly as in the app
    pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
    print("✅ CryptContext initialized with argon2")
except Exception as e:
    print(f"❌ Failed to initialize CryptContext: {e}")
    sys.exit(1)

password = "testpassword123"
print(f"\nTest Password: {password}")

# Hash
try:
    hashed = pwd_context.hash(password)
    print(f"✅ Generated Hash: {hashed}")
except Exception as e:
    print(f"❌ Hashing failed: {e}")
    sys.exit(1)

# Verify
try:
    is_valid = pwd_context.verify(password, hashed)
    print(f"✅ Immediate Verification Result: {is_valid}")
    
    if is_valid:
        print("🎉 SUCCESS: Logic is working correctly in isolation.")
    else:
        print("❌ FAILURE: Verification returned False for correct password.")
except Exception as e:
    print(f"❌ Verification crashed: {e}")
