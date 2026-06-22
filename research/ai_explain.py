"""
ai_explain.py — generates human-readable signal explanations in Bahasa Indonesia.

Template-based approach. No external API calls. Deterministic output.

Rules:
  - Never says "pasti naik", "pasti turun", "dijamin profit"
  - Never overrides the signal direction
  - Never invents data
  - Maximum ~250 words
  - 80% data, 20% interpretation

Data must be provided by the caller via the context dict.
"""
from typing import Optional


def generate_signal_explanation(ctx: dict) -> str:
    """
    Build the full AI ANALISA block. Returns a string suitable for
    inclusion in a Telegram message.

    Required keys in ctx:
        ticker, direction, confidence, regime, liquidity,
        ret_5_pct, vol_ratio,
        close_price, volume_today, volume_avg_20d,
        prices_5d, volumes_5d,
        holding_stats (dict or None)
    """
    lines = []

    # ── Header ──
    lines.append("🤖 AI ANALISA")
    lines.append("")

    # ── 1. Mengapa ──
    lines.append("Mengapa Signal Ini Muncul?")
    lines.append("")
    for point in _build_mengapa(ctx):
        lines.append(f"\u2022 {point}")
    lines.append("")

    # ── 2. Apa Yang Terjadi ──
    lines.append("Apa Yang Terjadi?")
    lines.append("")
    lines.append(_build_apa_terjadi(ctx))
    lines.append("")

    # ── 3. Data Pendukung ──
    lines.append("Data Pendukung")
    lines.append("")
    for line in _build_data_pendukung(ctx):
        lines.append(f"\u2022 {line}")
    lines.append("")

    # ── 4. Riwayat ──
    lines.append("Riwayat Signal Serupa")
    lines.append("")
    for line in _build_riwayat(ctx):
        lines.append(f"\u2022 {line}")
    lines.append("")

    # ── 5. Risiko ──
    lines.append("Risiko Yang Perlu Diperhatikan")
    lines.append("")
    lines.append(_build_risiko(ctx))

    return "\n".join(lines)


# ── Helpers ────────────────────────────────────────────────────


def _build_mengapa(ctx: dict) -> list[str]:
    points = []

    ticker = ctx["ticker"]
    direction = ctx["direction"]
    ret_pct = ctx["ret_5_pct"]
    vol_ratio = ctx["vol_ratio"]
    regime = ctx["regime"]
    liquidity = ctx["liquidity"]
    confidence = ctx["confidence"]

    # Price movement
    if ret_pct < -2:
        points.append(
            f"Harga {ticker} turun {abs(ret_pct):.1f}% "
            f"dalam 5 hari terakhir."
        )
    elif ret_pct < 0:
        points.append(
            f"Harga {ticker} turun dalam beberapa hari terakhir."
        )
    elif ret_pct > 2:
        points.append(
            f"Harga {ticker} naik {ret_pct:.1f}% "
            f"dalam 5 hari terakhir."
        )
    else:
        points.append(
            f"Harga {ticker} bergerak relatif stabil "
            f"dalam 5 hari terakhir."
        )

    # Volume
    if vol_ratio > 2.0:
        points.append(
            f"Volume transaksi meningkat sangat signifikan "
            f"({vol_ratio:.1f}x lipat rata-rata)."
        )
    elif vol_ratio > 1.3:
        points.append(
            f"Volume transaksi meningkat cukup signifikan."
        )
    elif vol_ratio < 0.7:
        points.append(
            f"Volume transaksi lebih rendah dari rata-rata."
        )
    else:
        points.append(
            f"Volume transaksi berada di sekitar rata-rata normal."
        )

    # Divergence pattern (price vs volume direction mismatch)
    if direction == "BUY" and ret_pct < -1 and vol_ratio > 1.2:
        points.append(
            "Harga turun namun volume naik — "
            "pola ini sering menunjukkan adanya akumulasi."
        )
    elif direction == "SELL" and ret_pct > 1 and vol_ratio < 0.8:
        points.append(
            "Harga naik namun volume rendah — "
            "pola ini sering menunjukkan distribusi."
        )

    # Market regime
    if regime == "bear":
        points.append(
            "Kondisi pasar sedang berada dalam fase bearish (melemah)."
        )
    elif regime == "bull":
        points.append(
            "Kondisi pasar sedang berada dalam fase bullish (menguat)."
        )
    else:
        points.append(
            "Kondisi pasar sedang sideways (bergerak datar)."
        )

    # Liquidity
    if liquidity == "large":
        points.append(
            f"{ticker} termasuk saham large cap dengan likuiditas tinggi."
        )
    elif liquidity == "mid":
        points.append(
            f"{ticker} termasuk saham mid cap dengan likuiditas menengah."
        )

    # Confidence
    if confidence >= 85:
        points.append(
            "Tingkat keyakinan sinyal berada pada level tinggi "
            "berdasarkan data historis."
        )
    elif confidence >= 70:
        points.append(
            "Tingkat keyakinan sinyal berada pada level yang cukup."
        )

    return points


def _build_apa_terjadi(ctx: dict) -> str:
    ticker = ctx["ticker"]
    direction = ctx["direction"]
    ret_pct = ctx["ret_5_pct"]
    vol_ratio = ctx["vol_ratio"]
    regime = ctx["regime"]

    if direction == "BUY":
        if ret_pct < -2 and vol_ratio > 1.5:
            pct_text = f"{abs(ret_pct):.1f}%"
            return (
                f"Dalam 5 hari terakhir harga {ticker} bergerak turun "
                f"sebesar {pct_text}, namun volume transaksi justru meningkat "
                f"di atas rata-rata. Kondisi ini sering disebut sebagai "
                f"volume divergence — ketika harga menurun tetapi minat "
                f"transaksi justru naik. Pada pasar yang sedang "
                f"{'melemah' if regime == 'bear' else 'bergerak'}, "
                f"sinyal divergence pada saham likuid seperti {ticker} "
                f"secara historis memiliki potensi yang cukup baik."
            )
        elif ret_pct < -1 and vol_ratio > 1.1:
            return (
                f"Harga {ticker} mengalami penurunan dalam beberapa hari "
                f"terakhir dengan volume transaksi yang meningkat. "
                f"Peningkatan volume saat harga turun dapat mengindikasikan "
                f"adanya aktivitas pembelian yang mulai menyerap "
                f"tekanan jual yang terjadi di pasar."
            )
        else:
            return (
                f"Sistem mendeteksi perubahan pada harga dan volume "
                f"{ticker} yang sesuai dengan pola volume divergence. "
                f"Namun sinyal saat ini belum menunjukkan konfirmasi "
                f"yang sangat kuat. Tetap pantau pergerakan harga "
                f"dalam beberapa hari ke depan."
            )

    else:  # SELL
        if ret_pct > 2 and vol_ratio < 0.7:
            pct_text = f"{ret_pct:.1f}%"
            return (
                f"Dalam 5 hari terakhir harga {ticker} naik sebesar "
                f"{pct_text}, namun volume transaksi justru menurun "
                f"di bawah rata-rata. Kondisi ini mengindikasikan "
                f"bahwa kenaikan harga tidak didukung oleh minat "
                f"pasar yang kuat. Sinyal bearish divergence seperti "
                f"ini perlu diwaspadai sebagai potensi koreksi."
            )
        else:
            return (
                f"Sistem mendeteksi perubahan pada harga dan volume "
                f"{ticker} yang sesuai dengan pola bearish divergence. "
                f"Pantau pergerakan harga untuk konfirmasi lebih lanjut."
            )


def _build_data_pendukung(ctx: dict) -> list[str]:
    lines = []

    prices = ctx.get("prices_5d", [])
    volumes = ctx.get("volumes_5d", [])
    close_price = ctx["close_price"]
    volume_today = ctx["volume_today"]
    volume_avg = ctx["volume_avg_20d"]
    ret_pct = ctx["ret_5_pct"]

    if len(prices) >= 5:
        lines.append(f"Harga 5 hari lalu: {prices[0]:,.0f}")

    lines.append(f"Harga hari ini: {close_price:,.0f}")

    if len(prices) >= 5:
        arah = "turun" if ret_pct < 0 else "naik"
        lines.append(f"Perubahan: {arah} {abs(ret_pct):.1f}%")

    if len(volumes) >= 5:
        lines.append(f"Volume 5 hari lalu: {volumes[0]:,.0f}")

    lines.append(f"Volume hari ini: {volume_today:,.0f}")
    lines.append(f"Rata-rata volume 20 hari: {volume_avg:,.0f}")

    if volume_avg > 0:
        kali = volume_today / volume_avg
        if kali >= 1.5:
            desc = "sangat tinggi"
        elif kali >= 1.2:
            desc = "cukup tinggi"
        elif kali >= 0.8:
            desc = "normal"
        else:
            desc = "cukup rendah"

        lines.append(
            f"Volume hari ini sekitar {kali:.1f} kali "
            f"dibanding rata-rata 20 hari terakhir ({desc})."
        )

    return lines


def _build_riwayat(ctx: dict) -> list[str]:
    lines = []
    stats = ctx.get("holding_stats")

    if not stats or stats.get("sample_size", 0) < 10:
        lines.append(
            "Belum ada cukup data historis untuk "
            "melakukan perbandingan."
        )
        return lines

    lines.append(
        f"Jumlah data historis: {stats['sample_size']}"
    )

    win_rate = stats.get("win_rate", 0)
    lines.append(
        f"Tingkat keberhasilan: "
        f"{win_rate:.1f}%"
    )

    ret = stats.get("avg_return", 0)
    if ret >= 0:
        lines.append(f"Rata-rata return: +{ret:.2f}%")
    else:
        lines.append(f"Rata-rata return: {ret:.2f}%")

    mean_days = stats.get("mean_days")
    if mean_days is not None:
        lines.append(
            f"Rata-rata waktu penyelesaian: {mean_days} "
            f"hari perdagangan"
        )

    return lines


def _build_risiko(ctx: dict) -> str:
    stats = ctx.get("holding_stats", {})
    direction = ctx["direction"]
    regime = ctx.get("regime", "")
    vol_ratio = ctx.get("vol_ratio", 1.0)

    risks = []

    # Stats-based risk
    if stats:
        tp = stats.get("tp_rate", 0)
        if tp < 40:
            risks.append(
                "Berdasarkan data historis, kurang dari separuh sinyal "
                "serupa mencapai target harga."
            )

        avg_ret = stats.get("avg_return", 0)
        if avg_ret < 0:
            risks.append(
                "Rata-rata return historis dari sinyal serupa "
                "bernilai negatif — perlu kehati-hatian ekstra."
            )

    # General risk
    if direction == "BUY":
        risks.append(
            "Jika tekanan jual masih berlanjut, harga dapat "
            "bergerak turun terlebih dahulu sebelum mencapai "
            "target yang ditentukan oleh sistem."
        )
    else:
        risks.append(
            "Jika minat beli masih kuat, harga dapat "
            "bergerak naik terlebih dahulu sebelum mencapai "
            "target penurunan."
        )

    # Regime risk
    if regime == "bear":
        risks.append(
            "Kondisi pasar yang sedang melemah dapat "
            "memperpanjang waktu yang dibutuhkan "
            "untuk mencapai target."
        )

    # Volume risk
    if vol_ratio > 5.0:
        risks.append(
            "Volume yang sangat tinggi dapat mengindikasikan "
            "spekulasi sesaat — bukan akumulasi yang stabil."
        )

    if not risks:
        return (
            "Gunakan manajemen risiko yang baik. "
            "Selalu patuhi stop loss yang sudah ditentukan."
        )

    return " ".join(risks)