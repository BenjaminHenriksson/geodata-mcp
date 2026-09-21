"""Reusable route documentation; paths and parameters are defined by FastAPI routes."""
from typing import Annotated

from fastapi import Depends, Path
from fastapi.security import APIKeyCookie

from viewer_auth import COOKIE_NAME

VIEW_EXAMPLE = 'v_123456789012345678901234'
ViewId = Annotated[str, Path(description='Unguessable map capability ID.', examples=[VIEW_EXAMPLE])]
LayerRef = Annotated[str, Path(description='Vector layer: ref.<table> or ws_<8 hex digits>.<table>.',
                                examples=['ref.byggnader'])]
VIEW_QUERY = 'Map capability ID granting access to this layer. Missing: 400; unknown view or excluded layer: 403.'
MANAGER_AUTH = [Depends(APIKeyCookie(name=COOKIE_NAME, scheme_name='managerCookie', auto_error=False,
                                    description='Signed HttpOnly cookie from POST /login; contains the key ID, never the API key.'))]


def content(description, media_type='application/json', schema=None, example=None):
    body = {'schema': schema or {'type': 'object'}}
    if example is not None:
        body['example'] = example
    return {'description': description, 'content': {media_type: body}}


def errors(descriptions):
    return {int(code): content(description, schema={
        'type': 'object', 'required': ['detail'], 'properties': {'detail': {'type': 'string'}},
    }, example={'detail': description}) for code, description in descriptions.items()}


MANAGER_DISABLED = errors({'503': 'Workspace manager disabled; set VIEWER_SECRET.'})
UNKNOWN_VIEW = errors({'404': 'Unknown view.'})
DATA_ERRORS = errors({'400': 'Invalid parameters, missing view or layer has no geometry.',
                       '403': 'Unknown view or layer is not part of the view.',
                       '404': 'Unknown layer or layer table not found.'})
REDIRECT_LOGIN = {302: {'description': 'Not signed in; redirect to /login.'}}
BINARY = {'type': 'string', 'format': 'binary'}
GEOJSON = {
    'type': 'object', 'required': ['type', 'features'],
    'properties': {'type': {'const': 'FeatureCollection'}, 'features': {'type': 'array', 'items': {
        'type': 'object', 'required': ['type', 'geometry', 'properties'],
        'properties': {'type': {'const': 'Feature'}, 'geometry': {'type': ['object', 'null']},
                       'properties': {'type': ['object', 'null']}}}}},
}
ETAG = {'ETag': {'description': 'View, layer metadata and compiler fingerprint.', 'schema': {'type': 'string'}}}
