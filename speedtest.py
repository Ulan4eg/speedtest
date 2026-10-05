#!/usr/bin/env python3
"""Замер скорости интернета: N скачиваний одного URL.

Примеры:
    python speedtest.py https://example.com/big_image.jpg
    python speedtest.py https://example.com/big_image.jpg -n 5 --warmup 2
    python speedtest.py https://example.com/big_image.jpg -j 4      # 4 потока
    python speedtest.py https://example.com/big_image.jpg --no-cache-bust

Что измеряется для каждого запроса:
    TTFB      - время от начала запроса до получения заголовков ответа
                (DNS + TCP + TLS + отправка запроса + ожидание сервера);
    передача  - время скачивания тела ответа после получения заголовков;
    скорость  - размер / время передачи (т.е. без учёта задержки соединения).
"""
import argparse
import http.client
import socket
import ssl
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

CHUNK = 64 * 1024  # читаем ответ кусками по 64 КБ
MB = 1_000_000


class DownloadError(Exception):
    """Ответ получен, но он некорректный (например, обрыв посреди загрузки)."""


@dataclass
class Result:
    size: int       # байт получено
    ttfb: float     # сек до получения заголовков
    transfer: float  # сек на скачивание тела

    @property
    def total(self) -> float:
        return self.ttfb + self.transfer

    @property
    def speed(self) -> float:
        """МБ/с по времени передачи (без TTFB)."""
        return self.size / max(self.transfer, 1e-6) / MB


def add_cache_buster(url: str) -> str:
    """Дописывает уникальный query-параметр, не трогая существующий query."""
    parts = urllib.parse.urlsplit(url)
    extra = f"_nocache={uuid.uuid4().hex}"
    query = f"{parts.query}&{extra}" if parts.query else extra
    return urllib.parse.urlunsplit(parts._replace(query=query))


def download(url: str, timeout: float, cache_bust: bool) -> Result:
    target = add_cache_buster(url) if cache_bust else url
    req = urllib.request.Request(
        target,
        headers={
            "User-Agent": "speedtest/2.0",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Accept-Encoding": "identity",  # без сжатия, меряем реальный объём
        },
    )
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        headers_at = time.perf_counter()
        expected = resp.headers.get("Content-Length")
        total = 0
        while chunk := resp.read(CHUNK):
            total += len(chunk)
        end = time.perf_counter()

    if expected is not None and expected.isdigit() and total != int(expected):
        raise DownloadError(f"получено {total} байт из {expected}")
    return Result(total, headers_at - start, end - headers_at)


def safe_download(url, timeout, cache_bust):
    """Возвращает (Result, None) или (None, текст ошибки)."""
    try:
        return download(url, timeout, cache_bust), None
    except urllib.error.HTTPError as e:  # раньше URLError: это его подкласс
        return None, f"HTTP {e.code} {e.reason}"
    except urllib.error.URLError as e:  # DNS, отказ в соединении, TLS при коннекте
        return None, f"сетевая ошибка: {e.reason}"
    except socket.timeout:  # таймаут во время чтения тела
        return None, "таймаут"
    except (ConnectionError, ssl.SSLError, http.client.HTTPException,
            DownloadError) as e:  # обрыв/сброс соединения, битый ответ
        return None, f"{type(e).__name__}: {e}"


def stats_row(label: str, values: list, fmt: str) -> None:
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    cells = [statistics.fmean(values), statistics.median(values),
             min(values), max(values), sd]
    print(f"{label:<24}" + "".join(format(c, f">10{fmt}") for c in cells))


def main() -> int:
    p = argparse.ArgumentParser(description="Замер скорости интернета")
    p.add_argument("url", help="адрес файла (лучше тяжёлая картинка, от 5 МБ)")
    p.add_argument("-n", "--requests", type=int, default=10,
                   help="количество запросов (по умолчанию 10)")
    p.add_argument("-j", "--threads", type=int, default=1,
                   help="параллельных потоков (по умолчанию 1 = последовательно)")
    p.add_argument("-w", "--warmup", type=int, default=1,
                   help="прогревочных запросов, не входят в итог (по умолчанию 1)")
    p.add_argument("-t", "--timeout", type=float, default=30,
                   help="таймаут сетевой операции, сек (по умолчанию 30)")
    p.add_argument("--no-cache-bust", action="store_true",
                   help="не добавлять уникальный параметр к URL "
                        "(для подписанных ссылок или замера именно CDN-кеша)")
    args = p.parse_args()

    if urllib.parse.urlsplit(args.url).scheme not in ("http", "https"):
        p.error("URL должен начинаться с http:// или https://")
    if args.requests < 1 or args.threads < 1 or args.warmup < 0:
        p.error("--requests и --threads должны быть >= 1, --warmup >= 0")
    threads = min(args.threads, args.requests)
    cache_bust = not args.no_cache_bust

    # 1. Прогрев: DNS-кеш, первое соединение, "холодный" старт сервера
    for i in range(1, args.warmup + 1):
        res, err = safe_download(args.url, args.timeout, cache_bust)
        if err:
            print(f"Прогрев {i}/{args.warmup}: ошибка: {err}")
            return 1
        print(f"Прогрев {i}/{args.warmup}: {res.size / MB:.2f} МБ за {res.total:.3f} с (не учитывается)")

    # 2. Основной замер
    print(f"\nЗамер: {args.requests} запросов, потоков: {threads}, "
          f"обход кеша: {'да' if cache_bust else 'нет'}")
    results, errors = [], 0
    wall_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=threads) as pool:
        futures = [pool.submit(safe_download, args.url, args.timeout, cache_bust)
                   for _ in range(args.requests)]
        for i, fut in enumerate(futures, 1):
            res, err = fut.result()
            if err:
                errors += 1
                print(f"[{i}/{args.requests}] ошибка: {err}")
                continue
            results.append(res)
            print(f"[{i}/{args.requests}] {res.size / MB:.2f} МБ | TTFB {res.ttfb:.3f} с | "
                  f"передача {res.transfer:.3f} с | {res.speed:.2f} МБ/с")
    wall = time.perf_counter() - wall_start

    if not results:
        print("\nНи один запрос не удался.")
        return 1

    # 3. Итоги
    total_bytes = sum(r.size for r in results)
    throughput = total_bytes / wall / MB
    print(f"\n--- Итог (успешно {len(results)} из {args.requests}) ---")
    print(f"{'':<24}" + "".join(f"{h:>10}" for h in
                                ("среднее", "медиана", "мин", "макс", "σ")))
    stats_row("TTFB, с", [r.ttfb for r in results], ".3f")
    stats_row("Передача, с", [r.transfer for r in results], ".3f")
    stats_row("Время запроса, с", [r.total for r in results], ".3f")
    stats_row("Скорость запроса, МБ/с", [r.speed for r in results], ".2f")
    print(f"\nСкачано всего:           {total_bytes / MB:.2f} МБ за {wall:.2f} с")
    print(f"Пропускная способность:  {throughput:.2f} МБ/с ({throughput * 8:.2f} Мбит/с)")
    if threads > 1:
        print("(параллельный режим: скорость запроса - на один поток, "
              "пропускная способность - суммарно по каналу)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nПрервано пользователем")
        sys.exit(130)
