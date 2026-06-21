#!/usr/bin/env python3
"""
orb_lock_calibrate.py — turn an observed phase-drift measurement into the
`--lock DEG_PER_REV` value for orb_encode.py.

Background
----------
A plain static image precesses on the blade because the render path free-runs:
it paints C_hw = column_clock_rate * Hall_period columns per physical revolution,
and C_hw != the authored 2016 unless the (open-loop) motor sits at the exact design
RPM. The image rotates by (C_hw - 2016) columns/rev. We cancel it by baking the
opposite rotation into the content (orb_encode.py --lock).

You only need to measure how fast the *unlocked* static image spins.

Two ways to measure
-------------------
A) Time one full apparent turn of the unlocked image (easiest):
     orb_lock_calibrate.py --turn-seconds 28.5 --dir cw [--rps 25]
   "--dir" is the direction the image appears to rotate (cw or ccw).

B) Film it and read degrees/second directly:
     orb_lock_calibrate.py --deg-per-sec 12.6 --dir cw [--rps 25]

--rps is the blade's revolutions/sec (frames/sec). Nominal 25 (~1500 RPM). If you
can read the real Hall period off the unit, use rps = 1e6 / hall_period_us.

The printed magnitude is a STARTING value. Push once, watch the blade:
  * still            -> done.
  * drifts same way  -> increase |--lock| (or this tool under-measured the turn time).
  * drifts opposite  -> flip the sign of --lock (or decrease |--lock|).
Two or three pushes bisect to a dead lock.
"""
import sys

FPS_NOMINAL = 25.0
CPR = 2016


def main():
    a = sys.argv
    rps = FPS_NOMINAL
    if '--rps' in a:
        rps = float(a[a.index('--rps') + 1])
    direction = None
    if '--dir' in a:
        direction = a[a.index('--dir') + 1].lower()
        if direction not in ('cw', 'ccw'):
            sys.exit('--dir must be cw or ccw')

    if '--deg-per-sec' in a:
        dps = float(a[a.index('--deg-per-sec') + 1])
    elif '--turn-seconds' in a:
        t = float(a[a.index('--turn-seconds') + 1])
        if t <= 0:
            sys.exit('--turn-seconds must be > 0')
        dps = 360.0 / t
    else:
        print(__doc__)
        sys.exit(1)

    deg_per_rev = dps / rps
    # The image spins in `direction`; --lock must rotate content the OPPOSITE way to
    # cancel. orb_encode rotates the source by (lock * i) degrees CCW (PIL positive).
    # So to cancel a CW drift we rotate CCW (positive lock); for CCW drift, negative.
    sign = +1.0 if direction == 'cw' else -1.0 if direction == 'ccw' else +1.0
    lock = sign * deg_per_rev

    n = int(round(360.0 / abs(deg_per_rev)))
    dur_ms = int(round(n / rps * 1000.0))
    col_err = deg_per_rev / 360.0 * CPR

    print(f'observed drift : {dps:.3f} deg/s'
          + (f' ({direction})' if direction else ''))
    print(f'blade rate     : {rps:.3f} rev/s')
    print(f'precession     : {deg_per_rev:.4f} deg/rev  (~{col_err:.2f} columns/rev; '
          f'C_hw ~ {CPR + col_err:.1f})')
    print()
    print(f'  --lock {lock:+.4f}')
    if direction is None:
        print('  (no --dir given: sign is a guess; flip if it drifts the same way)')
    print(f'seamless loop  : {n} frames = {n/rps:.2f}s  ->  push duration {dur_ms} ms')
    print()
    print('Example:')
    print(f'  python3 tools/orb_encode.py logo.png locked.bin --lock {lock:+.4f}')
    print(f'  bash    tools/stage_anims.sh -o locked.zip -d {dur_ms} locked.bin')
    print(f'  python3 server/orb_server.py --push-anims locked.zip --anim-character Ember \\')
    print(f'      --anim-version <V> --anim-media-function system --anim-as eb_idle_02')


if __name__ == '__main__':
    main()
