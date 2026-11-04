"""Listas grandes generadas desde datos verificados, separadas para no ensuciar el módulo.

`Mejores películas` reúne ganadoras del Óscar y del Globo de Oro a mejor película,
mejor actor y mejor película internacional. Cada identificador se contrastó contra
`title.basics` de IMDb: los tres que no coincidían se corrigieron antes de publicar.

`IMDb top` se calculó con los datos oficiales de IMDb (`title.ratings` y `title.basics`,
https://datasets.imdbws.com/), tomando películas con al menos 200.000 votos y series
con al menos 80.000, ordenadas por nota media. El comentario de cada línea guarda el
título, el año y la nota del día en que se generó la lista: 8 de septiembre de 2026.
"""

AWARD_WINNERS = [
    'tt28607951',  # Anora (2024)
    'tt14961016',  # I'm Still Here (2024)
    'tt8999762',  # The Brutalist (2024)
    'tt15398776',  # Oppenheimer (2023)
    'tt6710474',  # Everything Everywhere All at Once (2022)
    'tt14208870',  # The Fabelmans (2022)
    'tt13833688',  # The Whale (2022)
    'tt3704428',  # Elvis (2022)
    'tt10366460',  # CODA (2021)
    'tt14039582',  # Drive My Car (2021)
    'tt10293406',  # The Power of the Dog (2021)
    'tt9620288',  # King Richard (2021)
    'tt9770150',  # Nomadland (2020)
    'tt10288566',  # Another Round (2020)
    'tt10272386',  # The Father (2020)
    'tt10514222',  # Ma Rainey's Black Bottom (2020)
    'tt6751668',  # Parasite (2019)
    'tt8579674',  # 1917 (2019)
    'tt7286456',  # Joker (2019)
    'tt6966692',  # Green Book (2018)
    'tt6155172',  # Roma (2018)
    'tt1727824',  # Bohemian Rhapsody (2018)
    'tt5580390',  # The Shape of Water (2017)
    'tt5639354',  # A Fantastic Woman (2017)
    'tt5027774',  # Three Billboards Outside Ebbing, Missouri (2017)
    'tt4555426',  # Darkest Hour (2017)
    'tt4975722',  # Moonlight (2016)
    'tt5186714',  # The Salesman (2016)
    'tt4034228',  # Manchester by the Sea (2016)
    'tt1895587',  # Spotlight (2015)
    'tt3808342',  # Son of Saul (2015)
    'tt1663202',  # The Revenant (2015)
    'tt2562232',  # Birdman or (The Unexpected Virtue of Ignorance) (2014)
    'tt1065073',  # Boyhood (2014)
    'tt2980516',  # The Theory of Everything (2014)
    'tt2024544',  # 12 Years a Slave (2013)
    'tt2718492',  # Ida (2013)
    'tt2358891',  # The Great Beauty (2013)
    'tt0790636',  # Dallas Buyers Club (2013)
    'tt1024648',  # Argo (2012)
    'tt1602620',  # Amour (2012)
    'tt0443272',  # Lincoln (2012)
    'tt1655442',  # The Artist (2011)
    'tt1832382',  # A Separation (2011)
    'tt1033575',  # The Descendants (2011)
    'tt1504320',  # The King's Speech (2010)
    'tt1285016',  # The Social Network (2010)
    'tt1305806',  # The Secret in Their Eyes (2009)
    'tt0499549',  # Avatar (2009)
    'tt1263670',  # Crazy Heart (2009)
    'tt0887912',  # The Hurt Locker (2008)
    'tt1010048',  # Slumdog Millionaire (2008)
    'tt1013753',  # Milk (2008)
    'tt0477348',  # No Country for Old Men (2007)
    'tt0783233',  # Atonement (2007)
    'tt0469494',  # There Will Be Blood (2007)
    'tt0407887',  # The Departed (2006)
    'tt0405094',  # The Lives of Others (2006)
    'tt0449467',  # Babel (2006)
    'tt0455590',  # The Last King of Scotland (2006)
    'tt0388795',  # Brokeback Mountain (2005)
    'tt0379725',  # Capote (2005)
    'tt0375679',  # Crash (2004)
    'tt0338751',  # The Aviator (2004)
    'tt0350258',  # Ray (2004)
    'tt0167260',  # The Lord of the Rings: The Return of the King (2003)
    'tt0172495',  # Gladiator (2000)
    'tt0120338',  # Titanic (1997)
    'tt0118799',  # Life Is Beautiful (1997)
    'tt0109830',  # Forrest Gump (1994)
    'tt0108052',  # Schindler's List (1993)
    'tt0107818',  # Philadelphia (1993)
    'tt0105695',  # Unforgiven (1992)
    'tt0105323',  # Scent of a Woman (1992)
    'tt0102926',  # The Silence of the Lambs (1991)
    'tt0099348',  # Dances with Wolves (1990)
    'tt0097239',  # Driving Miss Daisy (1989)
    'tt0095765',  # Cinema Paradiso (1988)
    'tt0095953',  # Rain Man (1988)
    'tt0091763',  # Platoon (1986)
    'tt0073486',  # One Flew Over the Cuckoo's Nest (1975)
    'tt0071562',  # The Godfather Part II (1974)
    'tt0068646',  # The Godfather (1972)
    'tt0034583',  # Casablanca (1942)
]

IMDB_TOP_MOVIES = [
    'tt0111161',  # The Shawshank Redemption (1994) · 9.3
    'tt0068646',  # The Godfather (1972) · 9.2
    'tt0468569',  # The Dark Knight (2008) · 9.1
    'tt0167260',  # The Lord of the Rings: The Return of the King (2003) · 9.0
    'tt0108052',  # Schindler's List (1993) · 9.0
    'tt0071562',  # The Godfather Part II (1974) · 9.0
    'tt0050083',  # 12 Angry Men (1957) · 9.0
    'tt0120737',  # The Lord of the Rings: The Fellowship of the Ring (2001) · 8.9
    'tt1375666',  # Inception (2010) · 8.8
    'tt0137523',  # Fight Club (1999) · 8.8
    'tt0109830',  # Forrest Gump (1994) · 8.8
    'tt0110912',  # Pulp Fiction (1994) · 8.8
    'tt0167261',  # The Lord of the Rings: The Two Towers (2002) · 8.8
    'tt0060196',  # The Good, the Bad and the Ugly (1966) · 8.8
    'tt0816692',  # Interstellar (2014) · 8.7
    'tt0133093',  # The Matrix (1999) · 8.7
    'tt0080684',  # Star Wars: Episode V - The Empire Strikes Back (1980) · 8.7
    'tt0099685',  # GoodFellas (1990) · 8.7
    'tt0114369',  # Seven (1995) · 8.6
    'tt0102926',  # The Silence of the Lambs (1991) · 8.6
    'tt0120815',  # Saving Private Ryan (1998) · 8.6
    'tt0076759',  # Star Wars: Episode IV - A New Hope (1977) · 8.6
    'tt0120689',  # The Green Mile (1999) · 8.6
    'tt0103064',  # Terminator 2: Judgment Day (1991) · 8.6
    'tt0073486',  # One Flew Over the Cuckoo's Nest (1975) · 8.6
    'tt0245429',  # Spirited Away (2001) · 8.6
    'tt0317248',  # City of God (2002) · 8.6
    'tt0118799',  # Life Is Beautiful (1997) · 8.6
    'tt0038650',  # It's a Wonderful Life (1946) · 8.6
    'tt0047478',  # Seven Samurai (1954) · 8.6
    'tt15097216',  # Jai Bhim (2021) · 8.6
    'tt1853728',  # Django Unchained (2012) · 8.5
    'tt0172495',  # Gladiator (2000) · 8.5
    'tt0482571',  # The Prestige (2006) · 8.5
    'tt0407887',  # The Departed (2006) · 8.5
    'tt0088763',  # Back to the Future (1985) · 8.5
    'tt0110413',  # Léon: The Professional (1994) · 8.5
    'tt0120586',  # American History X (1998) · 8.5
    'tt0110357',  # The Lion King (1994) · 8.5
    'tt0114814',  # The Usual Suspects (1995) · 8.5
    'tt6751668',  # Parasite (2019) · 8.5
    'tt2582802',  # Whiplash (2014) · 8.5
    'tt1675434',  # The Intouchables (2011) · 8.5
    'tt0253474',  # The Pianist (2002) · 8.5
    'tt0054215',  # Psycho (1960) · 8.5
    'tt0034583',  # Casablanca (1942) · 8.5
    'tt10811166',  # The Kashmir Files (2022) · 8.5
    'tt9362722',  # Spider-Man: Across the Spider-Verse (2023) · 8.5
    'tt0095327',  # Grave of the Fireflies (1988) · 8.5
    'tt0064116',  # Once Upon a Time in the West (1968) · 8.5
]

IMDB_TOP_SERIES = [
    'tt0903747',  # Breaking Bad (2008) · 9.5
    'tt0185906',  # Band of Brothers (2001) · 9.4
    'tt0795176',  # Planet Earth (2006) · 9.4
    'tt5491994',  # Planet Earth II (2016) · 9.4
    'tt7366338',  # Chernobyl (2019) · 9.3
    'tt0417299',  # Avatar: The Last Airbender (2005) · 9.3
    'tt0306414',  # The Wire (2002) · 9.3
    'tt0944947',  # Game of Thrones (2011) · 9.2
    'tt0141842',  # The Sopranos (1999) · 9.2
    'tt12392504',  # Scam 1992: The Harshad Mehta Story (2020) · 9.2
    'tt2395695',  # Cosmos: A Spacetime Odyssey (2014) · 9.2
    'tt30263074',  # Sapne Vs Everyone (2023) · 9.2
    'tt2560140',  # Attack on Titan (2013) · 9.1
    'tt14392248',  # Aspirants (2021) · 9.1
    'tt1355642',  # Fullmetal Alchemist: Brotherhood (2009) · 9.1
    'tt1475582',  # Sherlock (2010) · 9.0
    'tt3032476',  # Better Call Saul (2015) · 9.0
    'tt0386676',  # The Office (2005) · 9.0
    'tt2861424',  # Rick and Morty (2013) · 9.0
    'tt11126994',  # Arcane (2021) · 9.0
    'tt0388629',  # One Piece (1999) · 9.0
    'tt2098220',  # Hunter X Hunter (2011) · 9.0
    'tt8420184',  # The Last Dance (2020) · 9.0
    'tt33043892',  # Dexter: Resurrection (2025) · 9.0
    'tt0103359',  # Batman: The Animated Series (1992) · 9.0
    'tt12004706',  # Panchayat (2020) · 9.0
    'tt14986406',  # Bleach: Thousand-Year Blood War (2022) · 9.0
    'tt0052520',  # The Twilight Zone (1959) · 9.0
    'tt1831164',  # Leyla and Mecnun (2011) · 9.0
    'tt9432978',  # Kota Factory (2019) · 9.0
    'tt10541088',  # Clarkson's Farm (2021) · 9.0
    'tt4742876',  # TVF Pitchers (2015) · 9.0
    'tt0877057',  # Death Note (2006) · 8.9
    'tt0303461',  # Firefly (2002) · 8.9
    'tt1865718',  # Gravity Falls (2012) · 8.9
    'tt0213338',  # Cowboy Bebop (1998) · 8.9
    'tt31938062',  # The Pitt (2025) · 8.9
    'tt10233448',  # Vinland Saga (2019) · 8.9
    'tt0200276',  # The West Wing (1999) · 8.9
    'tt22248376',  # Frieren: Beyond Journey's End (2023) · 8.9
    'tt0108778',  # Friends (1994) · 8.8
    'tt2356777',  # True Detective (2014) · 8.8
    'tt2802850',  # Fargo (2014) · 8.8
    'tt0098904',  # Seinfeld (1989) · 8.8
    'tt7660850',  # Succession (2018) · 8.8
    'tt0472954',  # It's Always Sunny in Philadelphia (2005) · 8.8
    'tt3398228',  # BoJack Horseman (2014) · 8.8
    'tt0214341',  # Dragon Ball Z (1996) · 8.8
    'tt0264235',  # Curb Your Enthusiasm (2000) · 8.8
    'tt7137906',  # When They See Us (2019) · 8.8
]
