import machine
import ntptime
import uasyncio as asyncio
import utime



def polish_utc_offset(year, month, day):
    def last_sunday(y, m):
        for d in range(31, 24, -1):
            try:
                wd = utime.localtime(
                    utime.mktime(
                        (y, m, d, 0, 0, 0, 0, 0)
                    )
                )[6]

                if wd == 6:
                    return d

            except:
                pass

    march = last_sunday(year, 3)
    october = last_sunday(year, 10)

    if (
            (month > 3 and month < 10)
            or (month == 3 and day >= march)
            or (month == 10 and day < october)
    ):
        return 2

    return 1


# How often to resync (s): rarely after success,
# more often after failure - without time, auto mode does not switch.
NTP_RESYNC_OK = 6 * 3600
NTP_RESYNC_FAIL = 120

_synced_once = False


async def ntp_task():
    """Periodic resync (clock drift, summer/winter time change)."""
    while True:
        await asyncio.sleep(
            NTP_RESYNC_OK if _synced_once else NTP_RESYNC_FAIL
        )

        try:
            await sync_ntp()
        except Exception as e:
            print("NTP task error:", e)


async def sync_ntp():
    global _synced_once

    await asyncio.sleep(2)

    ntptime.host = "pool.ntp.org"

    synced = False

    for _ in range(3):
        try:
            ntptime.settime()

            print("NTP synced")

            synced = True
            _synced_once = True

            t = utime.localtime()

            offset = polish_utc_offset(
                t[0],
                t[1],
                t[2]
            )

            now = utime.time() + offset * 3600
            tm = utime.localtime(now)

            machine.RTC().datetime((
                tm[0],
                tm[1],
                tm[2],
                tm[6] + 1,
                tm[3],
                tm[4],
                tm[5],
                0
            ))

            break

        except Exception as e:
            print(
                "NTP retry failed:",
                e
            )

            await asyncio.sleep(2)

    if not synced:
        print("NTP failed after retries")

    return synced
