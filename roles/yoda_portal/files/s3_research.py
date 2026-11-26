#!/usr/bin/env python3
from __future__ import annotations

__copyright__ = 'Copyright (c) 2021-2026, Utrecht University'
__license__   = 'GPLv3, see LICENSE'

import os
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import irods.keywords as kw
import magic
from flask import (
    Blueprint,
    g,
    jsonify,
    make_response,
    render_template,
    request,
    Response,
)
from flask import current_app as app
from irods.exception import CAT_NO_ROWS_FOUND
from irods.manager.data_object_manager import DataObjectManager
from irods.message import iRODSMessage
from irods.session import iRODSSession

import api
from cache_config import cache_view
from util import folder_template_path_check, get_parent_folders, log_error, unicode_secure_filename

research_bp = Blueprint('research_bp', __name__,
                        template_folder='templates',
                        static_folder='static/research',
                        static_url_path='/assets')

STALE_STREAM_TIMEOUT_SEC = 300.0
PUT_OPR = 1


@dataclass
class _OpenUpload:
    session: iRODSSession
    handle: Any
    expected_chunk: int
    last_activity: float
    lock: threading.Lock


active_streams: dict[tuple[str, str], _OpenUpload] = {}
streams_dict_lock = threading.Lock()


def _close_stream(stream: _OpenUpload, suppress_close_error: bool = True) -> None:
    try:
        if suppress_close_error:
            with suppress(Exception):
                stream.handle.close()
        else:
            stream.handle.close()
    finally:
        with suppress(Exception):
            stream.session.cleanup()


def _reap_stale_streams() -> None:
    now = time.monotonic()
    stale: list[_OpenUpload] = []
    with streams_dict_lock:
        stale_keys = [
            key for key, stream in active_streams.items()
            if now - stream.last_activity > STALE_STREAM_TIMEOUT_SEC
        ]
        for key in stale_keys:
            stream = active_streams.pop(key, None)
            if stream is not None:
                stale.append(stream)
    for stream in stale:
        with stream.lock:
            log_error("Closing stale upload stream")
            _close_stream(stream, suppress_close_error=True)


def _stale_stream_reaper() -> None:
    while True:
        time.sleep(30.0)
        _reap_stale_streams()


threading.Thread(target=_stale_stream_reaper, name='irods-upload-reaper', daemon=True).start()


def _write_chunk_direct(
    data_objects: DataObjectManager,
    path: str,
    number: int,
    size: int,
    data: Optional[bytes],
    resource: Optional[str],
    flow_id: Optional[str],
    total_chunks: int,
    total_size: Optional[int],
) -> None:
    """Write an upload chunk directly on the request thread with per-stream locking.

    Eliminates the single global writer thread and q.join() head-of-line blocking so
    concurrent users and parallel file uploads scale across WSGI worker threads.
    """
    key = (flow_id or path, path)

    if number == 1:
        with streams_dict_lock:
            existing = active_streams.pop(key, None)
        if existing is not None:
            with existing.lock:
                _close_stream(existing, suppress_close_error=True)

        stream_sess = data_objects.sess.clone()
        stream_sess.numThreads = 1
        open_opts: dict[str, Any] = {
            kw.OPR_TYPE_KW: PUT_OPR,
            kw.NUM_THREADS_KW: '1',
        }
        if resource:
            open_opts[kw.DEST_RESC_NAME_KW] = resource
        if total_size is not None and total_size >= 0:
            open_opts[kw.DATA_SIZE_KW] = str(total_size)

        obj_desc: Any = None
        try:
            obj_desc = stream_sess.data_objects.open(path, 'w', **open_opts)
            if data:
                obj_desc.write(data)
            if total_chunks <= 1:
                obj_desc.close()
                obj_desc = None
                with suppress(Exception):
                    stream_sess.cleanup()
                stream_sess = None  # type: ignore[assignment]
            else:
                stream = _OpenUpload(
                    session=stream_sess,
                    handle=obj_desc,
                    expected_chunk=2,
                    last_activity=time.monotonic(),
                    lock=threading.Lock(),
                )
                with streams_dict_lock:
                    active_streams[key] = stream
                obj_desc = None
                stream_sess = None  # type: ignore[assignment]
        finally:
            if obj_desc is not None:
                with suppress(Exception):
                    obj_desc.close()
            if stream_sess is not None:
                with suppress(Exception):
                    stream_sess.cleanup()
        return

    with streams_dict_lock:
        stream = active_streams.get(key)

    if stream is not None:
        with stream.lock:
            if number == stream.expected_chunk:
                try:
                    if data:
                        stream.handle.write(data)
                    stream.last_activity = time.monotonic()
                    stream.expected_chunk += 1
                    if number >= total_chunks:
                        with streams_dict_lock:
                            active_streams.pop(key, None)
                        _close_stream(stream, suppress_close_error=False)
                except Exception:
                    with streams_dict_lock:
                        active_streams.pop(key, None)
                    _close_stream(stream, suppress_close_error=True)
                    raise
                return
            # Out-of-order chunk: close streaming handle and fall back to seek+write
            with streams_dict_lock:
                active_streams.pop(key, None)
            _close_stream(stream, suppress_close_error=True)

    # Fallback for out-of-order or resumed chunk writes.
    stream_sess = data_objects.sess.clone()
    stream_sess.numThreads = 1
    fallback_opts: dict[str, Any] = {}
    if resource:
        fallback_opts[kw.DEST_RESC_NAME_KW] = resource
    try:
        with stream_sess.data_objects.open(path, 'a', **fallback_opts) as fd:
            fd.seek(int(size * (number - 1)))
            if data:
                fd.write(data)
    finally:
        with suppress(Exception):
            stream_sess.cleanup()


@research_bp.route('/')
@research_bp.route('/browse')
@cache_view()
def index() -> Response:
    return render_template('research/browse.html')


def build_object_path(path: str, relative_path: str, filename: str) -> str:
    relative_path = os.path.dirname(relative_path)
    path = path.lstrip("/")

    # Build relative path.
    if relative_path:
        base_dir = os.path.join("/" + g.irods.zone, 'home', path, relative_path)
        # Ensure upload collection exists.
        if not g.irods.collections.exists(base_dir):
            g.irods.collections.create(base_dir)
    else:
        base_dir = os.path.join("/" + g.irods.zone, 'home', path)

    return os.path.join(base_dir, filename)


def _file_is_text(text_string: str) -> bool:
    file_type = magic.from_buffer(text_string)
    return 'text' in file_type


@research_bp.route('/upload_folder_template', methods=['POST'])
def upload_folder_template() -> Response:
    file = request.files['file']

    # Check that file is text file
    try:
        file_content_pre = file.read().decode('utf-8')
    except Exception:
        response = make_response({'errors': ["File is not text file."]}, 400)
        response.headers["Content-Type"] = "application/json"
        return response

    # Limit file size to 1 MiB to prevent abuse or accidental DOS
    if len(file_content_pre) > 1048576:
        response = make_response({'errors': ["File exceeds the 1 MiB limit."]}, 400)
        response.headers["Content-Type"] = "application/json"
        return response

    # Check that file has valid content
    if not _file_is_text(file_content_pre[:2000]):
        response = make_response({'errors': ["File is empty."]}, 400)
        response.headers["Content-Type"] = "application/json"
        return response

    file_content = file_content_pre.split('\n')
    if not file_content:
        response = make_response({'errors': ["Could not read file content."]}, 500)
        response.headers["Content-Type"] = "application/json"
        return response

    # Parse current folder
    base_url = request.referrer
    parsed_url = urlparse(base_url)
    current_folder = parse_qs(parsed_url.query)['dir'][0]

    # Process template file
    folders = set()
    errors = []

    for i, line in enumerate(file_content):
        line = line.rstrip()
        if line != '' and not line.isspace():  # Skip lines that are empty or whitespaces
            if '\\' in line:  # If path contains backslash, replace with slash
                line = line.replace('\\', '/')
            if line.endswith('/'):  # If path ends with slash, remove it
                line = line.rstrip('/')

            error = folder_template_path_check(i, line)
            if error:
                errors.append(error)
            else:
                # Add all parent folders (and this folder) found in path
                folders.update(get_parent_folders(line))

    if len(folders) == 0:  # If no paths are in the list, no valid paths were found in file
        errors.append("No valid paths were found in file.")

    if len(errors) > 0:  # Do not process if there are errors
        log_error(
            "Error(s) occurred while processing the template file."
        )
        response = make_response({'errors': errors}, 400)
        response.headers["Content-Type"] = "application/json"
        return response

    # Sort folders before creating them
    folders_list = sorted(folders)

    # Create all folders
    base_path = f"/{g.irods.zone}/home"
    responses = []
    for folder in folders_list:
        parent = str(folder.parents[0])
        if parent == '/':  # If path has parents, append parents to collection
            coll = base_path + current_folder
        else:
            coll = base_path + current_folder + parent

        response = api.call('research_folder_add', data={'coll': coll, 'new_folder_name': folder.name})
        portal_response = {
            "folder": str(folder),
            "status": response['status'],
            "status_info": response['status_info']
        }
        responses.append(portal_response)

    response = make_response({"responses": responses}, 200)
    response.headers["Content-Type"] = "application/json"
    return response


@research_bp.route('/upload', methods=['GET'])
def upload_get() -> Response:
    flow_identifier = request.args.get('flowIdentifier', type=str)
    flow_chunk_number = request.args.get('flowChunkNumber', type=int)
    flow_total_chunks = request.args.get('flowTotalChunks', type=int)
    flow_chunk_size = request.args.get('flowChunkSize', type=int)
    flow_relative_path = request.args.get('flowRelativePath', type=str)

    flow_filename = request.args.get('flowFilename', type=str)
    flow_filename = unicode_secure_filename(flow_filename)

    filepath = request.args.get('filepath', type=str)

    if (not flow_identifier or not flow_filename or not flow_chunk_number
       or not flow_total_chunks or not flow_chunk_size or not filepath):
        # Parameters are missing or invalid.
        response = make_response(jsonify({"message": "Parameter missing or invalid"}), 500)
        response.headers["Content-Type"] = "application/json"
        return response

    object_path = build_object_path(filepath, flow_relative_path, flow_filename)

    # Partial file name for chunked uploads.
    if flow_total_chunks > 1 and app.config.get('UPLOAD_PART_FILES'):
        object_path = f"{object_path}.part"

    if flow_total_chunks == 1:
        # Ensuring single chunk files get to the overwrite stage as well
        response = make_response(jsonify({"message": "Chunk not found"}), 204)
        response.headers["Content-Type"] = "application/json"
        return response

    try:
        obj = g.irods.data_objects.get(object_path)

        if obj.replicas[0].size > int(flow_chunk_size * (flow_chunk_number - 1)):
            # Chunk already exists.
            response = make_response(jsonify({"message": "Chunk found"}), 200)
            response.headers["Content-Type"] = "application/json"
            return response
        else:
            raise Exception
    except Exception:
        # Chunk does not exist and needs to be uploaded.
        response = make_response(jsonify({"message": "Chunk not found"}), 204)
        response.headers["Content-Type"] = "application/json"
        return response


@research_bp.route('/upload', methods=['POST'])
def upload_post() -> Response:
    flow_identifier = request.form.get('flowIdentifier', type=str)
    flow_chunk_number = request.form.get('flowChunkNumber', type=int)
    flow_total_chunks = request.form.get('flowTotalChunks', type=int)
    flow_chunk_size = request.form.get('flowChunkSize', type=int)
    flow_total_size = request.form.get('flowTotalSize', type=int)
    flow_relative_path = request.form.get('flowRelativePath', type=str)

    flow_filename = request.form.get('flowFilename', type=str)
    flow_filename = unicode_secure_filename(flow_filename)

    filepath = request.form.get('filepath', type=str)

    if (not flow_identifier or not flow_filename or not flow_chunk_number
       or not flow_total_chunks or not flow_chunk_size or not filepath):
        # Parameters are missing or invalid.
        response = make_response(jsonify({"message": "Parameter missing or invalid"}), 500)
        response.headers["Content-Type"] = "application/json"
        return response

    object_path = build_object_path(filepath, flow_relative_path, flow_filename)

    # Partial file name for chunked uploads.
    if flow_total_chunks > 1 and app.config.get('UPLOAD_PART_FILES'):
        object_path = f"{object_path}.part"

    # Get the chunk data.
    chunk_data = request.files['file']
    encode_unicode_content = iRODSMessage.encode_unicode(chunk_data.stream.read())

    if flow_total_size is None and flow_total_chunks == 1:
        flow_total_size = len(encode_unicode_content)

    # Write chunk directly on this WSGI thread using per-stream locking (avoids global writer
    # thread head-of-line blocking, avoids truncate() on S3, and streams without O(k^2) caching).
    try:
        _write_chunk_direct(
            g.irods.data_objects,
            object_path,
            flow_chunk_number,
            flow_chunk_size,
            encode_unicode_content,
            app.config.get('IRODS_DEFAULT_RESC'),
            flow_id=flow_identifier,
            total_chunks=flow_total_chunks,
            total_size=flow_total_size,
        )
    except Exception as exc:
        log_error(f"Chunk upload failed for {object_path}: {exc}")
        response = make_response(jsonify({"message": "Chunk upload failed"}), 500)
        response.headers["Content-Type"] = "application/json"
        return response

    # Rename partial file name when complete for chunked uploads.
    if app.config.get('UPLOAD_PART_FILES') and flow_total_chunks > 1 and flow_total_chunks == flow_chunk_number:
        final_object_path = build_object_path(filepath, flow_relative_path, flow_filename)
        try:
            # overwriting doesn't work using the move command, therefore unlink the previous file first
            g.irods.data_objects.unlink(final_object_path, force=True)
        except CAT_NO_ROWS_FOUND:
            # No file was present, which is okay.
            pass
        except Exception as e:
            log_error(
                f"Error occurred on upload when unlinking existing object at {final_object_path} ({str(type(e))}:{str(e)})"
            )
            response = make_response(jsonify({"message": "Upload failed when removing existing data object."}), 500)
            response.headers["Content-Type"] = "application/json"
            return response

        g.irods.data_objects.move(object_path, final_object_path)

    response = make_response(jsonify({"message": "Chunk upload succeeded"}), 200)
    response.headers["Content-Type"] = "application/json"
    return response


@research_bp.route('/metadata/form')
def form() -> Response:
    path = request.args.get('path')
    return render_template('research/metadata-form.html', path=path)
