"""Deutsche Namen -> ESPN-Namen (englisch), damit /analyse auch 'Deutschland' oder 'Türkei' versteht."""
import unicodedata

ALIASES = {
    # Nationalmannschaften
    "deutschland": "germany", "frankreich": "france", "spanien": "spain", "italien": "italy",
    "england": "england", "niederlande": "netherlands", "holland": "netherlands", "belgien": "belgium",
    "portugal": "portugal", "kroatien": "croatia", "tschechien": "czechia", "turkei": "turkiye",
    "türkei": "turkiye", "turkey": "turkiye", "danemark": "denmark", "dänemark": "denmark",
    "norwegen": "norway", "schweden": "sweden", "schweiz": "switzerland", "osterreich": "austria",
    "österreich": "austria", "polen": "poland", "ungarn": "hungary", "serbien": "serbia",
    "griechenland": "greece", "schottland": "scotland", "wales": "wales", "irland": "republic of ireland",
    "nordirland": "northern ireland", "ukraine": "ukraine", "rumanien": "romania", "rumänien": "romania",
    "slowakei": "slovakia", "slowenien": "slovenia", "georgien": "georgia", "israel": "israel",
    "bosnien": "bosnia", "albanien": "albania", "finnland": "finland", "island": "iceland",
    "bulgarien": "bulgaria", "nordmazedonien": "north macedonia", "kosovo": "kosovo",
    "zypern": "cyprus", "armenien": "armenia", "estland": "estonia", "lettland": "latvia",
    "litauen": "lithuania", "luxemburg": "luxembourg", "montenegro": "montenegro", "moldau": "moldova",
    "kasachstan": "kazakhstan", "aserbaidschan": "azerbaijan", "weissrussland": "belarus",
    "weißrussland": "belarus", "belarus": "belarus", "färöer": "faroe islands", "faroer": "faroe islands",
    # Vereine mit deutschen Namen
    "bayern münchen": "bayern munich", "bayern munchen": "bayern munich", "köln": "cologne",
    "koln": "cologne", "1. fc köln": "cologne", "mönchengladbach": "monchengladbach",
    "gladbach": "monchengladbach", "inter mailand": "internazionale", "inter": "internazionale",
    "ac mailand": "ac milan", "mailand": "milan", "neapel": "napoli", "rom": "roma",
    "atletico madrid": "atletico", "sporting lissabon": "sporting", "benfica lissabon": "benfica",
    "rot-weiss": "", "hertha": "hertha",
}


def fold(text: str) -> str:
    """Kleinschreibung, Akzente/Umlaute entfernen: 'Türkiye' -> 'turkiye', 'München' -> 'munchen'."""
    text = text.lower().replace("ß", "ss")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).strip()


def search_terms(query: str) -> list[str]:
    q = query.lower().strip()
    terms = {fold(q)}
    for key in (q, fold(q)):
        if ALIASES.get(key):
            terms.add(fold(ALIASES[key]))
    return [t for t in terms if t]
