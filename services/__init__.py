from .facebook import FACEBOOK_HANDLER
from .instagram_and_x import INSTAGRAM_HANDLER , TWITTER_HANDLER
from .linkdin import LINKEDIN_HANDLER
from .pinterest import PINTEREST_HANDLER
from .spotify import SPOTIFY_HANDLER
from .youtube import YOUTUBE_HANDLER
from .genric import GENERIC_HANDLER
from .searcher import inline_search , inline_query_pin

all = [
    YOUTUBE_HANDLER,
    SPOTIFY_HANDLER,
    PINTEREST_HANDLER,
    LINKEDIN_HANDLER,
    INSTAGRAM_HANDLER,
    TWITTER_HANDLER,
    FACEBOOK_HANDLER,
    GENERIC_HANDLER,
    inline_search,
    inline_query_pin
]