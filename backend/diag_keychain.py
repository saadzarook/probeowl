# Author: @SaadZarook
"""Throwaway diagnostic for #93 (never merged): does a saved entry stay readable? Prints no value."""

import subprocess
import time
from secrets import token_hex

import keyring.backends.macOS as mac

from probeowl.secrets import delete_keychain, read_keychain, save_secret

name = f"probeowl_test_{token_hex(4)}.uat"
value = f"pw-{token_hex(12)}"
print("default:", subprocess.run(["security", "default-keychain"], capture_output=True, text=True).stdout.strip())
save_secret("probeowl", name, value)
for i in range(4):
    found = read_keychain("probeowl", name)
    rc = subprocess.run(["security", "find-generic-password", "-s", "probeowl", "-a", name], capture_output=True).returncode
    print(i, "read:", "same" if found == value else repr(found is None and "None" or "other"), "security rc:", rc)
    time.sleep(0.5)
k = mac.Keyring()
for i in range(3):
    got = mac.api.find_generic_password(None, "probeowl", name, not_found_ok=True)
    print("api", i, "same" if got == value else ("None" if got is None else "other"))
delete_keychain("probeowl", name)
print("after delete:", read_keychain("probeowl", name) is None)
