#!/usr/bin/env python3
from __future__ import annotations

__copyright__ = 'Copyright (c) 2021-2026, Utrecht University'
__license__   = 'GPLv3, see LICENSE'

import os
import queue
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


class Chunk:
    def __init__(self,
                 data_objects: Optional[DataObjectManager],
                 path: Optional[str],
                 number: int,
                 size: int,
                 data: Optional[bytes],
                 resource: Optional[str],
                 flow_id: Optional[str] = None,
                 total_chunks: int = 1,
                 total_size: Optional[int] = None) -> None:
        self.data_objects: Optional[DataObjectManager] = data_objects
        self.path: Optional[str] = path
        self.number: int = number
        self.size: int = size
        self.data: Optional[bytes] = data
        self.resource: Optional[str] = resource
        self.flow_id: Optional[str] = flow_id
        self.total_chunks: int = total_chunks
        self.total_size: Optional[int] = total_size


q: queue.Queue[Chunk] = queue.Queue(4)
active_streams: dict[tuple[str, str], _OpenUpload] = {}
failed_streams: set[tuple[str, str]] = set()
failed_streams_lock = threading.Lock()


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
    stale_keys = [
        key for key, stream in active_streams.items()
        if now - stream.last_activity > STALE_STREAM_TIMEOUT_SEC
    ]
    for key in stale_keys:
        stream = active_streams.pop(key, None)
        if stream is not None:
            log_error(f"Closing stale upload stream for {key[1]}")
            _close_stream(stream, suppress_close_error=True)


def irods_writer() -> None:
    while True:
        try:
            chunk = q.get(timeout=30.0)
        except queue.Empty:
            _reap_stale_streams()
            continue

        try:
            _reap_stale_streams()
            if chunk.path and chunk.data_objects is not None:
                key = (chunk.flow_id or chunk.path, chunk.path)

                if chunk.number == 1:
                    with failed_streams_lock:
                        failed_streams.discard(key)
                    existing = active_streams.pop(key, None)
                    if existing is not None:
                        _close_stream(existing, suppress_close_error=True)
                else:
                    with failed_streams_lock:
                        if key in failed_streams:
                            continue

                stream_sess: Optional[iRODSSession] = None
                obj_desc: Any = None
                try:
                    if chunk.number == 1:
                        # Clone session so connman GC cannot disconnect an active multi-chunk stream
                        # and isolate numThreads=1 for single-stream cacheless S3 multipart upload.
                        stream_sess = chunk.data_objects.sess.clone()
                        stream_sess.numThreads = 1
                        open_opts: dict[str, Any] = {
                            kw.OPR_TYPE_KW: PUT_OPR,
                            kw.NUM_THREADS_KW: '1',
                        }
                        if chunk.resource:
                            open_opts[kw.DEST_RESC_NAME_KW] = chunk.resource
                        if chunk.total_size is not None and chunk.total_size >= 0:
                            open_opts[kw.DATA_SIZE_KW] = str(chunk.total_size)

                        # Opening with 'w' sets O_WRONLY | O_CREAT | O_TRUNC, replacing existing
                        # objects on both S3 and unixfilesystem without needing RESOURCE_OP_TRUNCATE.
                        obj_desc = stream_sess.data_objects.open(chunk.path, 'w', **open_opts)
                        if chunk.data:
                            obj_desc.write(chunk.data)

                        if chunk.total_chunks <= 1:
                            try:
                                obj_desc.close()
                            finally:
                                with suppress(Exception):
                                    stream_sess.cleanup()
                        else:
                            active_streams[key] = _OpenUpload(
                                session=stream_sess,
                                handle=obj_desc,
                                expected_chunk=2,
                                last_activity=time.monotonic(),
                            )
                    else:
                        stream = active_streams.get(key)
                        if stream is not None and chunk.number == stream.expected_chunk:
                            if chunk.data:
                                stream.handle.write(chunk.data)
                            stream.last_activity = time.monotonic()
                            stream.expected_chunk += 1
                            if chunk.number >= chunk.total_chunks:
                                active_streams.pop(key, None)
                                _close_stream(stream, suppress_close_error=False)
                        else:
                            # Fallback for out-of-order or resumed chunk writes.
                            if stream is not None:
                                active_streams.pop(key, None)
                                _close_stream(stream, suppress_close_error=True)
                            stream_sess = chunk.data_objects.sess.clone()
                            stream_sess.numThreads = 1
                            fallback_opts: dict[str, Any] = {}
                            if chunk.resource:
                                fallback_opts[kw.DEST_RESC_NAME_KW] = chunk.resource
                            try:
                                with stream_sess.data_objects.open(chunk.path, 'a', **fallback_opts) as fd:
                                    fd.seek(int(chunk.size * (chunk.number - 1)))
                                    if chunk.data:
                                        fd.write(chunk.data)
                            finally:
                                with suppress(Exception):
                                    stream_sess.cleanup()
                except Exception as exc:
                    log_error(f"Chunk upload failed for {chunk.path}: {exc}")
                    aborted = active_streams.pop(key, None)
                    if aborted is not None:
                        _close_stream(aborted, suppress_close_error=True)
                    else:
                        if obj_desc is not None:
                            with suppress(Exception):
                                obj_desc.close()
                        if stream_sess is not None:
                            with suppress(Exception):
                                stream_sess.cleanup()
                    with failed_streams_lock:
                        failed_streams.add(key)
        finally:
            q.task_done()


threading.Thread(target=irods_writer, name='irods-writer', daemon=True).start()


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

    # Write chunk data via streaming writer (avoids data_objects.truncate(), which is unsupported
    # on S3 resources, and streams multi-chunk uploads without O(k^2) S3 cache re-downloads).
    q.put(Chunk(
        g.irods.data_objects,
        object_path,
        flow_chunk_number,
        flow_chunk_size,
        encode_unicode_content,
        app.config.get('IRODS_DEFAULT_RESC'),
        flow_id=flow_identifier,
        total_chunks=flow_total_chunks,
        total_size=flow_total_size,
    ))

    # check at the end, and after every Gb
    if flow_total_chunks == flow_chunk_number:
        need_to_check_flow = True
    else:
        flow_check_byte_interval = 2**30  # Check upload flow (at least) every 1 GB
        flow_check_chunk_interval = int(flow_check_byte_interval / min(flow_chunk_size, flow_check_byte_interval))
        need_to_check_flow = flow_chunk_number % flow_check_chunk_interval == 0

    if need_to_check_flow:
        q.put(Chunk(None, None, 0, 0, None, None))
        q.join()

    stream_key = (flow_identifier, object_path)
    with failed_streams_lock:
        has_failed = stream_key in failed_streams
        if has_failed and flow_total_chunks == flow_chunk_number:
            failed_streams.discard(stream_key)

    if has_failed:
        # Failure in upload writer thread.
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
