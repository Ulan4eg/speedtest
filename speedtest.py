#!/usr/bin/env python3
"""Замер скорости интернета: N последовательных скачиваний одного URL.

Пример:
    python speedtest.py https://example.com/big_image.jpg
    python speedtest.py https://example.com/big_image.jpg -n 5
"""
import argparse
import sys
import time
import urllib.request

CHUNK = 64 * 1024  # читаем ответ кусками по 64 КБ


def download(url: str, timeout: float) -> tuple[int, float]:
    """Скачивает url целиком, возвращает (байт получено, секунд затрачено)."""
    # Cache-Control просим не кешировать, чтобы мерить именно сеть
    req = urllib.request.Request(
        url, headers={"User-Agent": "speedtest/1.0", "Cache-Control": "no-cache"}
    )
    start = time.perf_counter()
    total = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        while chunk := resp.read(CHUNK):
            total += len(chunk)
    return total, time.perf_counter() - start


def main() -> int:
    parser = argparse.ArgumentParser(description="Замер скорости интернета")
    parser.add_argument("url", help="адрес файла (лучше тяжёлая картинка)")
    parser.add_argument("-n", "--requests", type=int, default=10,
                        help="количество запросов (по умолчанию 10)")
    parser.add_argument("-t", "--timeout", type=float, default=30,
                        help="таймаут запроса, сек (по умолчанию 30)")
    args = parser.parse_args()

    times, sizes = [], []
    for i in range(1, args.requests + 1):
        try:
            size, elapsed = download(args.url, args.timeout)
        except Exception as e:  # сеть, HTTP-ошибка, таймаут и т.п.
            print(f"[{i}/{args.requests}] ошибка: {e}")
            continue
        times.append(elapsed)
        sizes.append(size)
        speed = size / elapsed / 1_000_000
        print(f"[{i}/{args.requests}] {size / 1_000_000:.2f} МБ за {elapsed:.3f} с "
              f"({speed:.2f} МБ/с)")

    if not times:
        print("Ни один запрос не удался.")
        return 1

    total_bytes = sum(sizes)
    total_time = sum(times)
    avg_time = total_time / len(times)
    speed_mb = total_bytes / total_time / 1_000_000  # МБ/с

    print("\n--- Итог ---")
    print(f"Успешных запросов:     {len(times)} из {args.requests}")
    print(f"Среднее время запроса: {avg_time:.3f} с")
    print(f"Скачано всего:         {total_bytes / 1_000_000:.2f} МБ")
    print(f"Средняя скорость:      {speed_mb:.2f} МБ/с ({speed_mb * 8:.2f} Мбит/с)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
