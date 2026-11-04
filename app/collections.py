"""Colecciones editoriales propias, con referencias de Letterboxd documentadas."""
from app.catalog import Catalog
from app.showcase import AWARD_WINNERS, IMDB_TOP_MOVIES, IMDB_TOP_SERIES

SERIES='series'  # Una serie dentro de una lista de películas viaja con su tipo al lado.

# Comedias para levantar el ánimo: Ben Stiller, Jim Carrey, las series de Ricky Gervais
# y Louis CK, y las comedias mejor puntuadas en IMDb.
COMEDIA=[
    'tt0942385',            # Tropic Thunder (2008) · Ben Stiller
    'tt0196229',            # Zoolander (2001) · Ben Stiller
    'tt0212338',            # Meet the Parents (2000) · Ben Stiller
    'tt0129387',            # There's Something About Mary (1998) · Ben Stiller
    'tt0364725',            # Dodgeball (2004) · Ben Stiller
    'tt0359950',            # The Secret Life of Walter Mitty (2013) · Ben Stiller
    'tt0120382',            # The Truman Show (1998) · Jim Carrey
    'tt0110475',            # The Mask (1994) · Jim Carrey
    'tt0109686',            # Dumb and Dumber (1994) · Jim Carrey
    'tt0109040',            # Ace Ventura (1994) · Jim Carrey
    'tt0119528',            # Liar Liar (1997) · Jim Carrey
    'tt0315327',            # Bruce Almighty (2003) · Jim Carrey
    ('tt0290978',SERIES),   # The Office · Reino Unido (2001) · Ricky Gervais
    ('tt0445114',SERIES),   # Extras (2005) · Ricky Gervais
    ('tt8398600',SERIES),   # After Life (2019) · Ricky Gervais
    ('tt2616280',SERIES),   # Derek (2012) · Ricky Gervais
    ('tt1553644',SERIES),   # The Ricky Gervais Show (2010)
    ('tt1492966',SERIES),   # Louie (2010) · Louis CK
    ('tt5425186',SERIES),   # Horace and Pete (2016) · Louis CK
    'tt0118799',            # La vita è bella (1997) · de las comedias mejor puntuadas
    'tt0021749',            # City Lights (1931)
    'tt0027977',            # Modern Times (1936)
    'tt0032553',            # The Great Dictator (1940)
    'tt0053291',            # Some Like It Hot (1959)
    'tt0071853',            # Monty Python and the Holy Grail (1975)
    'tt0107048',            # Groundhog Day (1993)
    'tt2278388',            # The Grand Budapest Hotel (2014)
]

# Románticas: las mejor puntuadas en IMDb y las que pidió la casa, con sus vecinas.
ROMANCE=[
    'tt3783958',            # La La Land (2016)
    'tt0332280',            # The Notebook (2004)
    'tt0218967',            # The Family Man (2000)
    'tt0120632',            # City of Angels (1998)
    'tt0112883',            # Don Juan DeMarco (1994)
    'tt0034583',            # Casablanca (1942)
    'tt0046250',            # Roman Holiday (1953)
    'tt0095765',            # Nuovo Cinema Paradiso (1988)
    'tt0099653',            # Ghost (1990)
    'tt0108160',            # Sleepless in Seattle (1993)
    'tt0112471',            # Before Sunrise (1995)
    'tt0112579',            # The Bridges of Madison County (1995)
    'tt0120338',            # Titanic (1997)
    'tt0119643',            # Meet Joe Black (1998)
    'tt0120889',            # What Dreams May Come (1998)
    'tt0125439',            # Notting Hill (1999)
    'tt0211915',            # Amélie (2001)
    'tt0314331',            # Love Actually (2003)
    'tt0338013',            # Eternal Sunshine of the Spotless Mind (2004)
    'tt0381681',            # Before Sunset (2004)
    'tt0414387',            # Pride & Prejudice (2005)
    'tt0431308',            # P.S. I Love You (2007)
    'tt1022603',            # (500) Days of Summer (2009)
    'tt1045658',            # Silver Linings Playbook (2012)
    'tt1798709',            # Her (2013)
]

# Marvel y DC mejor puntuadas en IMDb, animación incluida.
HEROES=[
    'tt0468569',            # The Dark Knight (2008)
    'tt0372784',            # Batman Begins (2005)
    'tt1345836',            # The Dark Knight Rises (2012)
    'tt1877830',            # The Batman (2022)
    'tt7286456',            # Joker (2019)
    'tt0078346',            # Superman (1978)
    'tt0451279',            # Wonder Woman (2017)
    'tt0409459',            # Watchmen (2009)
    'tt0434409',            # V for Vendetta (2005)
    'tt0371746',            # Iron Man (2008)
    'tt0848228',            # The Avengers (2012)
    'tt4154756',            # Avengers: Infinity War (2018)
    'tt4154796',            # Avengers: Endgame (2019)
    'tt1843866',            # Captain America: The Winter Soldier (2014)
    'tt3498820',            # Captain America: Civil War (2016)
    'tt2015381',            # Guardians of the Galaxy (2014)
    'tt3501632',            # Thor: Ragnarok (2017)
    'tt1825683',            # Black Panther (2018)
    'tt3315342',            # Logan (2017)
    'tt1431045',            # Deadpool (2016)
    'tt6263850',            # Deadpool & Wolverine (2024)
    'tt1877832',            # X-Men: Days of Future Past (2014)
    'tt10872600',           # Spider-Man: No Way Home (2021)
    'tt4633694',            # Spider-Man: Into the Spider-Verse (2018) · animada
    'tt9362722',            # Spider-Man: Across the Spider-Verse (2023) · animada
    'tt0106364',            # Batman: Mask of the Phantasm (1993) · animada
    ('tt0103359',SERIES),   # Batman: The Animated Series (1992) · animada
    ('tt3322312',SERIES),   # Daredevil (2015)
]

# Divulgación premiada y ciencia ficción con ciencia o historia detrás.
CIENCIA=[
    ('tt0081846',SERIES),   # Cosmos: A Personal Voyage (1980) · Carl Sagan
    ('tt2395695',SERIES),   # Cosmos: A Spacetime Odyssey (2014)
    ('tt0795176',SERIES),   # Planet Earth (2006)
    ('tt5491994',SERIES),   # Planet Earth II (2016)
    ('tt0296310',SERIES),   # The Blue Planet (2001)
    ('tt6769208',SERIES),   # Blue Planet II (2017)
    ('tt9253866',SERIES),   # Our Planet (2019)
    ('tt0071075',SERIES),   # The World at War (1973) · Segunda Guerra Mundial
    ('tt0185906',SERIES),   # Band of Brothers (2001) · Segunda Guerra Mundial
    ('tt0374463',SERIES),   # The Pacific (2010) · Segunda Guerra Mundial
    ('tt7366338',SERIES),   # Chernobyl (2019)
    'tt8760684',            # Apollo 11 (2019) · documental del espacio
    'tt7775622',            # Free Solo (2018) · documental premiado
    'tt0062622',            # 2001: A Space Odyssey (1968)
    'tt0075860',            # Close Encounters of the Third Kind (1977)
    'tt0083658',            # Blade Runner (1982)
    'tt1856101',            # Blade Runner 2049 (2017)
    'tt0118884',            # Contact (1997)
    'tt1182345',            # Moon (2009)
    'tt0470752',            # Ex Machina (2014)
    'tt2543164',            # Arrival (2016)
    'tt0816692',            # Interstellar (2014)
    'tt3659388',            # The Martian (2015)
    'tt1454468',            # Gravity (2013)
    'tt1160419',            # Dune (2021)
    'tt15239678',           # Dune: Part Two (2024)
    'tt0112384',            # Apollo 13 (1995)
    'tt0086197',            # The Right Stuff (1983)
    'tt15398776',           # Oppenheimer (2023)
    'tt2084970',            # The Imitation Game (2014)
    'tt0268978',            # A Beautiful Mind (2001)
    'tt2980516',            # The Theory of Everything (2014)
    'tt4846340',            # Hidden Figures (2016)
    'tt0107290',            # Jurassic Park (1993) · sobre el libro de Michael Crichton
    'tt0120737',            # The Lord of the Rings: The Fellowship of the Ring (2001)
    'tt0167261',            # The Lord of the Rings: The Two Towers (2002)
    'tt0167260',            # The Lord of the Rings: The Return of the King (2003)
    'tt0108052',            # La lista de Schindler (1993) · Segunda Guerra Mundial
    'tt0120815',            # Saving Private Ryan (1998) · Segunda Guerra Mundial
    'tt5013056',            # Dunkirk (2017) · Segunda Guerra Mundial
]

# (título, tipo, identificadores). El tipo hace falta para pedir la ficha correcta a Cinemeta.
COLLECTIONS=[
    ('Mejores películas · premiadas','movie',AWARD_WINNERS),
    ('Mejores películas según IMDb','movie',IMDB_TOP_MOVIES),
    ('Mejores series según IMDb','series',IMDB_TOP_SERIES),
    ('Para que no te gane la depresión','movie',COMEDIA),
    ('Me estremezco compadre','movie',ROMANCE),
    ('Para niños rata','movie',HEROES),
    ('Para ñoños','movie',CIENCIA),
]


def entries(media_type,items):
    """Cada lista declara un tipo y los títulos marcados aparte conservan el suyo."""
    return [(item,media_type) if isinstance(item,str) else item for item in items]


def shelves(store):
    with store.connect() as conn:
        ids=[identifier for _,media_type,items in COLLECTIONS
             for identifier,__ in entries(media_type,items)]
        rows=conn.execute('SELECT id,data FROM content WHERE id=ANY(%s)',(ids,)).fetchall()
    by_id=dict(rows)
    # El orden de cada lista es intencional (nota IMDb o año del premio) y se conserva.
    return [{'title':title,
             'items':[by_id[identifier] for identifier,__ in entries(media_type,items) if identifier in by_id]}
            for title,media_type,items in COLLECTIONS]


def missing(store):
    """Identificadores de las colecciones que todavía no tienen ficha guardada."""
    with store.connect() as conn:
        known={row[0] for row in conn.execute('SELECT id FROM content').fetchall()}
    return [(item_type,identifier) for _,media_type,items in COLLECTIONS
            for identifier,item_type in entries(media_type,items) if identifier not in known]


async def seed(store,client):
    """Descarga las fichas que falten. Un título retirado de Cinemeta no detiene al resto."""
    catalog=Catalog(store,client)
    pending=missing(store)
    print(f'Fichas por descargar: {len(pending)}',flush=True)
    for media_type,identifier in pending:
        try:
            item=await catalog.detail(media_type,identifier)
            print(' ',identifier,item['name'],item.get('releaseInfo',''),flush=True)
        except Exception as error:
            print(' ',identifier,'no disponible:',type(error).__name__,flush=True)
    print('Colecciones listas.',flush=True)
