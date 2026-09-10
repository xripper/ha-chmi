# ČHMÚ pro Home Assistant

[![HACS custom repository](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Otevřít v HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=xripper&repository=ha-chmi&category=integration)

Integrace otevřených dat **Českého hydrometeorologického ústavu** pro Home
Assistant. Poskytuje aktuální měření z vybrané stanice, radarový kompozit CZRAD
složený nad mapou ČR, výstrahy z oficiálního CAP feedu a textovou předpověď pro
kraj.

English version: [README.md](README.md)

## Co integrace vytvoří

| Entita | Popis |
| --- | --- |
| `weather.<stanice>` | Aktuální stav měřený na stanici. Bez předpovědi – viz [Proč není předpověď](#proč-není-předpověď). |
| `sensor.<stanice>_srazky_dnes` | Úhrn srážek od lokální půlnoci, sečtený z vlastních vzorků stanice. |
| `sensor.<stanice>_srazky_doma_dnes` | Úhrn srážek v poloze Home Assistantu od lokální půlnoci, ze spojeného produktu radar + srážkoměry. Dále jako klouzavý senzor `_za_hodinu` a `_24_h`. |
| `sensor.<stanice>_*` | Senzor pro každý prvek, který stanice publikuje: teplota (i přízemní 5 cm a půdní 5–100 cm), vlhkost, tlak, rosný bod, rychlost a směr větru, nárazy, srážky, výška sněhu, sluneční svit, globální a rozptýlené záření, oblačnost, dohlednost, kód stavu počasí. |
| `camera.<stanice>_meteoradar` | Nejnovější radarový snímek (krok 5 minut) ocropovaný na georeferencovanou datovou oblast a složený nad hranicemi krajů. |
| `sensor.<stanice>_intenzita_srazek_z_radaru` | Intenzita srážek v mm/h odečtená z radarového pixelu nad polohou Home Assistantu. |
| `binary_sensor.<stanice>_vystraha` | Zapnuto, dokud pro váš kraj výstraha ČHMÚ platí (ne před jejím začátkem); atributy jsou kompatibilní s [MeteoalarmCard](https://github.com/MrBartusek/MeteoalarmCard). |
| `sensor.<stanice>_aktivni_vystrahy` | Počet platných výstrah, všechny v atributech; výstrahy vydané na později jsou v atributu `upcoming`. |
| `sensor.<stanice>_textova_predpoved` | Předpověď psaná meteorologem pro váš kraj, dnes a zítra. |

Entity vzniknou jen pro prvky, které stanice skutečně měří. Ze 475 stanic
s aktuálními daty měří 296 teplotu, 432 srážky, 208 vítr, 84 tlak a jen 34
oblačnost – seznam entit se tedy mezi stanicemi hodně liší.

## Instalace

### HACS

1. HACS → menu tří teček → **Vlastní repozitáře**
2. Repozitář `https://github.com/xripper/ha-chmi`, kategorie **Integrace**
3. Nainstalovat **ČHMÚ** a restartovat Home Assistant
4. **Nastavení → Zařízení a služby → Přidat integraci → ČHMÚ**

### Ručně

Zkopírujte `custom_components/chmi` do `config/custom_components` a restartujte
Home Assistant.

## Nastavení

Instalační dialog nabídne všechny stanice řazené podle vzdálenosti od polohy
Home Assistantu, včetně vzdálenosti a výšky v popisku; už nastavené stanice
v seznamu nejsou. Radar, výstrahy a textová předpověď platí pro celou
republiku, proto se zapnou jen u první přidané stanice.

- **⋮ → Konfigurovat** přepne radar mezi **maximální odrazivostí** a **srážkami
  dopadajícími k zemi** a vypne či zapne srážky ve vlastní poloze, výstrahy
  a textovou předpověď.
- **⋮ → Překonfigurovat** přesune položku na jinou stanici. Entity si zachovají
  svá id i historii, protože jsou identifikované podle položky konfigurace, ne
  podle stanice.

## Jak svěží data jsou

| Data | Publikace | Dotazování |
| --- | --- | --- |
| Staniční měření | denní soubor se přepisuje **1× za hodinu**, kolem HH:02 UTC, nejnovější vzorek je z ≈ HH−1:50 UTC | každých 15 min (podmíněný dotaz, nezměněný soubor nic nestojí) |
| Radarový kompozit | každých 5 minut, s odstupem 1–3 minuty od času snímku | každých 5 min |
| Výstrahy (CAP) | při změně | každých 10 min |
| Textová předpověď | několikrát denně | každé 3 h |

**Hodnoty ze stanice jsou tedy 10 až 70 minut staré.** Je to vlastnost otevřených
dat, ne integrace – ČHMÚ rychlejší zdroj staničních měření nepublikuje. Hodnoty
starší než tři hodiny se zahazují, místo aby se opakovaly, takže porouchané
čidlo udělá svůj senzor nedostupným; totéž platí pro radar, který zešedne, když
není publikován snímek novější než 30 minut.

## Datové zdroje

Vše pochází z níže uvedených veřejných služeb, žádný účet ani API klíč není
potřeba.

- Stanice: `https://opendata.chmi.cz/meteorology/climate/now/`
  (`metadata/meta1-*.json` seznam stanic, `metadata/meta2-*.json` prvky podle
  stanice, `data/10m-*.json` a `data/1h-*.json` měření)
- Radar: `https://opendata.chmi.cz/meteorology/weather/radar/composite/maxz/png/`
  a `.../png_masked/`, název souboru
  `pacz2gmaps3.z_max3d.YYYYMMDD.HHMM.0.png` (UTC)
- Spojené srážky:
  `https://opendata.chmi.cz/meteorology/weather/radar/composite/merge1h/hdf5/`,
  název souboru `T_PASV23_C_OKPR_YYYYMMDDhhmmss.hdf` (UTC, konec okna)
- Výstrahy: `https://vystrahy-cr.chmi.cz/data/XOCZ50_OKPR.xml` (CAP v1.2).
  Výstraha se ke kraji přiřazuje podle geokódů CISORP jejích oblastí, takže
  fungují i výstrahy na úrovni ORP a výstrahy pro více krajů.
- Textová předpověď:
  `https://opendata.chmi.cz/meteorology/weather/forecast/now/web_pCK{0,1}tx_R{kraj}_*.json`

### Georeference radaru

Publikovaný PNG má 680×460 px, ale mapová data nesou jen jeho levé dolní
**598×378 px**; zbylé pruhy jsou vertikální průřezy a textový popisek. Datová
oblast je v EPSG:3857 s pixelem 1 km (1555,68 m v Mercatoru) a pokrývá
**11,267°–19,624° v. d., 48,047°–51,458° s. š.** Integrace na tuto oblast
cropuje, vymaže řádky s popiskem a odrazivost odečítá z jediného pixelu, ve
kterém leží daný bod, porovnáním jeho barvy s oficiální dBZ škálou (okolní
pixely se použijí jen tehdy, když je pixel překryt vykreslenou hranicí).
Intenzita srážek vychází z Z = 200 R^1,6; škála má krok 4 dBZ, takže odečet
může odrazivost podhodnotit o jednu třídu.

### Denní úhrn srážek

ČHMÚ publikuje oficiální denní úhrny jen za klimatologický den 07–07 lokálního
času a vydává je jednou měsíčně v `climate/recent/data/daily/`. Senzor
`sensor.<stanice>_srazky_dnes` proto sčítá vlastní desetiminutové úhrny stanice
(hodinové tam, kde stanice desetiminutovou řadu nemá) za probíhající
**kalendářní den v lokálním čase**, takže se s číslem, které ČHMÚ později vydá
jako denní úhrn, shodovat nebude. Vzorek nese úhrn intervalu končícího jeho
časovou značkou, takže ten stamplý přesně o půlnoci patří předchozímu dni a
nezapočítá se. V českém čase den začíná ve 22:00 UTC, proto se čtou oba denní
soubory — jsou to tytéž soubory jako pro ostatní senzory a dotazy jsou
podmíněné.

### Srážky ve vlastní poloze

`merge1h` je produkt ČHMÚ, který spojuje radarové pole srážek s údaji vlastních
i partnerských srážkoměrů metodou krigingu s externím driftem. Na 267 párech
stanice×hodina se hodnota v místě srážkoměru shoduje s tím srážkoměrem
v průměru na 0,006 mm (největší rozdíl 0,70 mm) — mezi stanicemi je to tedy
radarové pole ohnuté na srážkoměry, což je nejblíž srážkoměru na vlastní
zahradě, co otevřená data dovolí.

Produkt existuje jen jako **60minutová okna publikovaná každých 10 minut**,
proto:

- `_za_hodinu` je nejnovější okno, tedy posledních 60 minut,
- `_dnes` a `_24_h` sčítají celé hodiny, jejichž okna se nepřekrývají, takže
  denní úhrn má zpoždění až jednu hodinu (atribut `covered_to` říká, kam sahá,
  `hours_missing` kolik oken ČHMÚ nevydal).

Hodnoty se čtou z HDF5 mřížky, jsou to tedy přesné milimetry produktu, ne
barevné třídy. Soubor má ~35 kB; po restartu se doplní celý probíhající den
(až 24 souborů), pak se stahuje jeden soubor za hodinu. Ke čtení slouží
[`pyfive`](https://pypi.org/project/pyfive/), čistě pythonový HDF5 reader, který
se na Home Assistant OS nainstaluje bez kompilátoru.

### Jak se odvozuje stav počasí

Staniční soubory obsahují měření, ne stav počasí, proto entita použije nejlepší
dostupný zdroj v tomto pořadí:

1. staniční kód stavu počasí (`ww`, tabulky WMO 4677 a 4680), pokud ho stanice
   hlásí – hlásí ho 35 stanic
2. měřené srážky nebo radarová odrazivost nad souřadnicemi stanice, rozlišené
   podle teploty
   a odrazivosti na déšť, silný déšť, smíšené srážky, sněžení nebo bouřku
3. dohlednost pod 1 km → mlha
4. oblačnost v osminách (`N`), 34 stanic
5. sluneční svit nebo globální záření v porovnání s modelem jasné oblohy, jen
   za světla

Stanice, která nic z toho neměří, ponechá stav `neznámý` místo dohadu; stejně se
po západu slunce zachovají stanice bez údaje o oblačnosti.

## Proč není předpověď

ČHMÚ publikuje model ALADIN jen jako GRIB2 (≈ 12–30 MB na proměnnou pro
1km českou domenu, 60–100 MB pro 2,3km Lambert). Použitelná podmnožina by
znamenala ≈ 120 MB na běh modelu čtyřikrát denně a navíc dekodér GRIB2, který
na Home Assistant OS nelze nainstalovat. Místo toho integrace poskytuje textovou
předpověď psanou meteorology ČHMÚ.

## Vývoj

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-test.txt
pytest          # 155 testů, fixtures jsou reálné odpovědi ČHMÚ
ruff check .
```

## Licence a atribuce

Kód je pod licencí MIT. Data ČHMÚ jsou publikována pod
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) a každá entita nese
atribuci `Data © Český hydrometeorologický ústav (CC BY 4.0)`. Přiložené hranice
krajů v `custom_components/chmi/data/cz_regions.geojson` jsou zjednodušené
veřejné administrativní hranice ČR. Obrázky v `custom_components/chmi/brand/`
zobrazují logo ČHMÚ, které patří ČHMÚ a slouží jen k označení zdroje dat.

Projekt není spojen s ČHMÚ.
