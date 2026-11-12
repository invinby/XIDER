import time

import crypto


def test_nonce_window_accepts_3000_unique_messages_and_evicts_oldest():
    with crypto._seen_lock:
        crypto._seen.clear()
        crypto._seen_set.clear()
    timestamp = time.time()
    try:
        assert all(crypto._mark_seen(f"nonce-{index}", timestamp) for index in range(3000))
        with crypto._seen_lock:
            assert len(crypto._seen) == crypto._SEEN_NONCES_MAX == 2048
            assert len(crypto._seen_set) == len(crypto._seen)
            assert crypto._seen[0][0] == "nonce-952"
    finally:
        with crypto._seen_lock:
            crypto._seen.clear()
            crypto._seen_set.clear()
