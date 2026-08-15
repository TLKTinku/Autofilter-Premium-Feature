import re
import aiohttp
import warnings
import logging
from io import BytesIO
from PIL import Image
from info import DREAMXBOTZ_IMAGE_FETCH, TMDB_API_KEY
from imdb import Cinemagoer


logger = logging.getLogger(__name__)
ia = Cinemagoer()
LONG_IMDB_DESCRIPTION = False

def list_to_str(lst):
    if lst:
        return ", ".join(map(str, lst))
    return ""





Image.MAX_IMAGE_PIXELS = None
warnings.simplefilter("ignore", Image.DecompressionBombWarning)
async def fetch_image(url, size=(860, 1200)):
    if not DREAMXBOTZ_IMAGE_FETCH:
        logger.info("Image fetching is disabled.")
        return None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.error(f"Failed to fetch image: {response.status}")
                    return None

                data = await response.read()
                img = Image.open(BytesIO(data))
                img = img.resize(size, Image.LANCZOS)


                out = BytesIO()
                img.save(out, format="JPEG")
                out.seek(0)
                return out

    except aiohttp.ClientError as e:
        logger.error(f"HTTP request error in fetch_image: {e}")
    except IOError as e:
        logger.error(f"I/O error in fetch_image: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in fetch_image: {e}")

    return None


async def get_movie_details(query, id=False, file=None):
    try:
        if not id:
            query = query.strip().lower()
            title = query
            year = re.findall(r'[1-2]\d{3}$', query, re.IGNORECASE)
            if year:
                year = list_to_str(year[:1])
                title = query.replace(year, "").strip()
            elif file is not None:
                year = re.findall(r'[1-2]\d{3}', file, re.IGNORECASE)
                if year:
                    year = list_to_str(year[:1])
            else:
                year = None
            movieid = ia.search_movie(title.lower(), results=10)
            if not movieid:
                return None
            if year:
                filtered = list(filter(lambda k: str(k.get('year')) == str(year), movieid))
                if not filtered:
                    filtered = movieid
            else:
                filtered = movieid
            movieid = list(filter(lambda k: k.get('kind') in ['movie', 'tv series'], filtered))
            if not movieid:
                movieid = filtered
            movieid = movieid[0].movieID
        else:
            movieid = query
        movie = ia.get_movie(movieid)
        ia.update(movie, info=['main', 'vote details'])
        if movie.get("original air date"):
            date = movie["original air date"]
        elif movie.get("year"):
            date = movie.get("year")
        else:
            date = "N/A"
        plot = movie.get('plot')
        if plot and len(plot) > 0:
            plot = plot[0]
        else:
            plot = movie.get('plot outline')
        if plot and len(plot) > 800:
            plot = plot[:800] + "..."
        poster_url = movie.get('full-size cover url')
        return {
            'title': movie.get('title'),
            'votes': movie.get('votes'),
            "aka": list_to_str(movie.get("akas")),
            "seasons": movie.get("number of seasons"),
            "box_office": movie.get('box office'),
            'localized_title': movie.get('localized title'),
            'kind': movie.get("kind"),
            "imdb_id": f"tt{movie.get('imdbID')}",
            "cast": list_to_str(movie.get("cast")),
            "runtime": list_to_str(movie.get("runtimes")),
            "countries": list_to_str(movie.get("countries")),
            "certificates": list_to_str(movie.get("certificates")),
            "languages": list_to_str(movie.get("languages")),
            "director": list_to_str(movie.get("director")),
            "writer": list_to_str(movie.get("writer")),
            "producer": list_to_str(movie.get("producer")),
            "composer": list_to_str(movie.get("composer")),
            "cinematographer": list_to_str(movie.get("cinematographer")),
            "music_team": list_to_str(movie.get("music department")),
            "distributors": list_to_str(movie.get("distributors")),
            'release_date': date,
            'year': movie.get('year'),
            'genres': list_to_str(movie.get("genres")),
            'poster_url': poster_url,
            'plot': plot,
            'rating': str(movie.get("rating", "N/A")),
            'url': f'https://www.imdb.com/title/tt{movieid}'
        }
    except Exception as e:
        logger.error(f"An error occurred in get_movie_details: {e}")
        return None

async def get_movie_detailsx(query, id=False, file=None):
    """
    Fetches movie/TV details directly from the official TMDB API
    (no third-party proxy involved).
    """
    tmdb_base = "https://api.themoviedb.org/3"
    img_base = "https://image.tmdb.org/t/p"

    try:
        async with aiohttp.ClientSession() as session:
            if not id:
                q = str(query).strip()
                year = None
                year_match = re.findall(r'[1-2]\d{3}$', q)
                if year_match:
                    year = year_match[0]
                    q = q.replace(year, "").strip()
                elif file is not None:
                    year_match = re.findall(r'[1-2]\d{3}', file)
                    if year_match:
                        year = year_match[0]

                search_params = {"api_key": TMDB_API_KEY, "query": q, "include_adult": "false"}
                async with session.get(f"{tmdb_base}/search/multi", params=search_params) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        logger.error(f"TMDB search failed [{resp.status}] for query={q}\n{text}")
                        return None
                    search_data = await resp.json()

                results = [r for r in search_data.get("results", []) if r.get("media_type") in ("movie", "tv")]
                if not results:
                    return None

                if year:
                    matched = [
                        r for r in results
                        if str(r.get("release_date") or r.get("first_air_date") or "")[:4] == str(year)
                    ]
                    if matched:
                        results = matched

                top = results[0]
                media_type = top.get("media_type")
                tmdb_id = top.get("id")
            else:
                # 'query' is expected to be a TMDB id in this branch; try movie first, then tv
                media_type = "movie"
                tmdb_id = query

            detail_params = {"api_key": TMDB_API_KEY, "append_to_response": "credits,external_ids"}
            async with session.get(f"{tmdb_base}/{media_type}/{tmdb_id}", params=detail_params) as resp:
                if resp.status != 200 and id:
                    # fallback: maybe it's a TV id, not a movie id
                    media_type = "tv"
                    async with session.get(f"{tmdb_base}/{media_type}/{tmdb_id}", params=detail_params) as resp2:
                        if resp2.status != 200:
                            text = await resp2.text()
                            logger.error(f"TMDB details failed [{resp2.status}] for id={tmdb_id}\n{text}")
                            return None
                        data = await resp2.json()
                elif resp.status != 200:
                    text = await resp.text()
                    logger.error(f"TMDB details failed [{resp.status}] for id={tmdb_id}\n{text}")
                    return None
                else:
                    data = await resp.json()
    except Exception as e:
        logger.error(f"An error occurred in get_movie_detailsx: {e}")
        return None

    def names(items, key="name"):
        return [i.get(key) for i in (items or []) if i.get(key)]

    def crew_by_job(crew, job):
        return [c.get("name") for c in (crew or []) if c.get("job") == job]

    credits = data.get("credits", {}) or {}
    cast_list = [c.get("name") for c in (credits.get("cast") or [])[:10] if c.get("name")]
    crew = credits.get("crew") or []
    external_ids = data.get("external_ids", {}) or {}

    poster_path = data.get("poster_path")
    backdrop_path = data.get("backdrop_path")

    details = {}
    details['title'] = data.get('title') or data.get('name')
    details['localized_title'] = data.get('original_title') or data.get('original_name')
    release_date = data.get('release_date') or data.get('first_air_date') or ""
    details['release_date'] = release_date
    details['year'] = release_date[:4] if release_date else None
    details['rating'] = round(float(data.get('vote_average', 0)), 1) if data.get('vote_average') is not None else None
    details['votes'] = int(data.get('vote_count', 0) or 0)
    runtime = data.get('runtime')
    if runtime is None:
        ep_runtimes = data.get('episode_run_time') or []
        runtime = ep_runtimes[0] if ep_runtimes else None
    details['runtime'] = str(runtime) if runtime else None
    details['certificates'] = None
    details['tmdb_url'] = f"https://www.themoviedb.org/{data.get('media_type', 'movie')}/{data.get('id')}" if data.get('id') else None
    details['tmdb_id'] = data.get('id')
    details['imdb_id'] = external_ids.get('imdb_id') or data.get('imdb_id')

    details['genres'] = names(data.get('genres'))
    details['languages'] = [l.get('english_name') or l.get('name') for l in (data.get('spoken_languages') or [])]
    details['countries'] = [c.get('name') for c in (data.get('production_countries') or [])]

    details['director'] = crew_by_job(crew, 'Director')
    details['writer'] = crew_by_job(crew, 'Writer') or crew_by_job(crew, 'Screenplay')
    details['producer'] = crew_by_job(crew, 'Producer')
    details['composer'] = crew_by_job(crew, 'Original Music Composer')
    details['cinematographer'] = crew_by_job(crew, 'Director of Photography')
    details['cast'] = cast_list

    details['plot'] = [data.get('overview')] if data.get('overview') else None
    details['tagline'] = data.get('tagline')
    details['box_office'] = data.get('revenue') or None
    details['distributors'] = [c.get('name') for c in (data.get('production_companies') or [])]
    details['seasons'] = data.get('number_of_seasons')

    details['poster_url'] = f"{img_base}/w780{poster_path}" if poster_path else None
    details['backdrop_url'] = f"{img_base}/w1280{backdrop_path}" if backdrop_path else None

    return details

