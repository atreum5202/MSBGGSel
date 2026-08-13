#!/usr/bin/env python3
"""
MSB Patch Status Checker + Applier
Запускать из WSL: python3 /mnt/c/Users/Atreum/Desktop/MySoft/MSB/check_and_patch.py
"""
import os, sys, subprocess

SRC = '/home/atreum/chromium/src'
PATCHES = '/home/atreum/patches'

checks = [
    ('01', 'toolbar-badge',  'chrome/browser/ui/views/toolbar/toolbar_view.cc'),
    ('02', 'webdriver',      'third_party/blink/renderer/core/frame/navigator.cc'),
    ('03', 'canvas-noise',   'third_party/blink/renderer/modules/canvas/html_canvas_element.cc'),
    ('04', 'webgl-spoof',    'third_party/blink/renderer/modules/webgl/webgl_rendering_context_base.cc'),
    ('05', 'audio-noise',    'third_party/blink/renderer/modules/webaudio/audio_buffer.cc'),
    ('06', 'webrtc-leak',    'third_party/blink/renderer/modules/peerconnection/rtc_peer_connection.cc'),
]

print("=" * 60)
print("MSB PATCH STATUS")
print("=" * 60)

not_applied = []
for num, name, rel in checks:
    full = os.path.join(SRC, rel)
    patch_file = None
    for fn in os.listdir(PATCHES):
        if fn.startswith(num + '-') and fn.endswith('.patch'):
            patch_file = os.path.join(PATCHES, fn)
            break

    if not os.path.exists(full):
        print(f"  [{num}] {name}: FILE NOT FOUND ({rel})")
        continue

    with open(full, errors='replace') as f:
        content = f.read()

    applied = ('MSB' in content or 'msb-fingerprint' in content or
               'msb_noise' in content or 'msb_' in content or
               'msb-profile' in content)

    if applied:
        print(f"  [{num}] {name}: APPLIED OK")
    else:
        print(f"  [{num}] {name}: NOT APPLIED  <-- надо накатить")
        if patch_file:
            not_applied.append((num, name, patch_file, full))

print()

if not not_applied:
    print("Все патчи применены! Можно запускать сборку.")
    sys.exit(0)

print(f"Не применено: {len(not_applied)} патч(ей)")
print()

if '--apply' not in sys.argv:
    print("Запусти с --apply чтобы применить их:")
    print("  python3 /mnt/c/Users/Atreum/Desktop/MySoft/MSB/check_and_patch.py --apply")
    sys.exit(1)

print("Применяю патчи через git apply...")
os.chdir(os.path.join(SRC, '..'))  # chromium/

for num, name, patch_file, target in not_applied:
    print(f"\n  Накатываю [{num}] {name}...")
    result = subprocess.run(
        ['git', 'apply', '--whitespace=nowarn', patch_file],
        cwd=SRC,
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"    OK: патч [{num}] применён")
    else:
        print(f"    FAIL: {result.stderr.strip()}")
        print(f"    Пробую с --reject...")
        result2 = subprocess.run(
            ['git', 'apply', '--whitespace=nowarn', '--reject', patch_file],
            cwd=SRC,
            capture_output=True, text=True
        )
        if result2.returncode == 0:
            print(f"    OK (с --reject)")
        else:
            print(f"    ERROR: {result2.stderr.strip()}")
            print(f"    Патч [{num}] нужно применить вручную!")

print()
print("Готово. Проверяю итоговый статус...")
print()

# Финальная проверка
for num, name, rel in checks:
    full = os.path.join(SRC, rel)
    if not os.path.exists(full):
        print(f"  [{num}] {name}: FILE NOT FOUND")
        continue
    with open(full, errors='replace') as f:
        content = f.read()
    applied = ('MSB' in content or 'msb-fingerprint' in content or
               'msb_noise' in content or 'msb_' in content or 'msb-profile' in content)
    status = "APPLIED OK" if applied else "STILL NOT APPLIED"
    print(f"  [{num}] {name}: {status}")
