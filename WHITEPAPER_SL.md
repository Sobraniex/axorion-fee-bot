# Axorion Fee Bot — Bela knjiga (preprosto razloženo)

*Telegram bot, ki ti v trenutku sporoči, da je denarnica na Solani pobrala provizije (fee).*

---

## 1. Težava (v enem stavku)

Če imaš memecoin ali projekt na Solani, navadno obstaja **denarnica, ki pobira
provizije**. Edini način, da izveš, kdaj je dobila plačilo, je, da kot nor
osvežuješ block explorer. To je nadležno in stvari ti uidejo mimo.

## 2. Kaj ta bot počne

Botu daš naslov denarnice. Od tega trenutka naprej **takoj, ko ta denarnica
prejme SOL, ti zazvoni telefon** s Telegram sporočilom, kot je:

> 💰 **Provizija pobrana**
> Denarnica `4Nd1…x9Qd` je pravkar prejela **0,842000 SOL**
> [Poglej transakcijo](https://solscan.io)

To je celoten produkt. Brez prijavljanja v kontrolne plošče, brez osveževanja.
Obvestilo pride samo do tebe.

## 3. Kako deluje (brez tehničnega znanja)

Predstavljaj si Solano kot ogromen javni bančni izpisek, ki ga lahko bere
kdorkoli. Vsako plačilo je zapisano za vedno in vse je javno.

Bot naredi to, kar bi naredil skrben pomočnik:

1. **Daš mu naslov denarnice** (s pritiskom na gumb ali tako, da prilepiš
   naslov).
2. **Vsakih ~30 sekund** bot pogleda zadnjo aktivnost te denarnice v javnem
   zapisu Solane.
3. Za vsako novo transakcijo naredi en preprost izračun:
   *"Koliko SOL je imela ta denarnica prej in koliko potem?"*
   - Več kot prej → **denar je prišel noter** → to je pobrana provizija.
   - Enako ali manj → ni kaj poročati, bot je tiho.
4. Če je denar prišel noter, ti **pošlje Telegram sporočilo** s točnim zneskom in
   povezavo do potrdila (transakcija na Solscanu).

Nobenega ugibanja, nobenega notranjega dostopa. Samo bere javni zapis in
odšteva.

## 4. Uporaba — ima gumbe!

Ni se ti treba učiti ukazov na pamet. Pošlji `/start` in dobiš meni:

- **➕ Track a wallet** (Spremljaj denarnico) — pritisni, nato prilepi naslov.
- **📋 My wallets** (Moje denarnice) — vidiš vse, kar spremljaš; vsaka ima gumb
  **🗑 Remove** (Odstrani).
- **❓ Help** (Pomoč) — kratka razlaga.

Raje tipkaš? Ukazi še vedno delujejo:
`/track <denarnica>`, `/untrack <denarnica>`, `/list`.

## 5. Česa NE počne (iskren del)

- **Ne** dotika se tvojega denarja ali ključev. Samo *bere* javne podatke.
  Sredstev dobesedno ne more premikati — nikoli ne zahteva zasebnega ključa.
- **Ne** napoveduje ničesar. Poroča o tem, kar se je že zgodilo.
- Privzeto šteje **vsak prejeti SOL** kot "provizijo". Če denarnica prejme SOL iz
  kakšnega drugega razloga, to še vedno šteje. (To se da kasneje zožiti samo na
  specifične transakcije pobiranja provizij.)

## 6. Nastavitve, ki jih lahko prilagodiš

- **Utišaj majhne zneske:** nastaviš minimum, npr. obvesti me samo za provizije
  nad 0,1 SOL.
- **Hitreje ali počasneje:** 30-sekundni interval se da spremeniti.
- **Zanesljivost:** bot bere Solano prek "RPC" (vrata do blockchaina). Brezplačna
  javna vrata so počasna in natrpana; brezplačen račun pri ponudniku, kot je
  Helius, naredi obvestila hitra in zanesljiva.

## 7. Pod pokrovom (en odstavek za radovedne)

Napisano v Pythonu. Za klepet in gumbe uporablja uradno Telegram knjižnico za
bote, s Solano pa se pogovarja prek dveh standardnih klicev samo za branje:
`getSignaturesForAddress` (kaj se je pravkar zgodilo s to denarnico?) in
`getTransaction` (daj mi podrobnosti). Logika razlike v stanju je ena majhna
funkcija — pokrita je z avtomatskimi testi, da se ne more tiho pokvariti.

## 8. Je varno / ali deluje?

Osrednja logika "je provizija prišla?" je **testirana z avtomatskimi testi in
preverjena v živo na pravem Solana mainnetu** — pravilno bere aktivnost
denarnice in izračuna spremembo SOL. Telegram del teče na Telegramovi lastni
infrastrukturi, ko vstaviš bot žeton (brezplačen, od @BotFather).

---

*Ta dokument je namerno netehničen. Celotna koda in navodila za namestitev so v
istem repozitoriju.*
