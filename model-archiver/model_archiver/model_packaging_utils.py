# Copyright 2018 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# Licensed under the Apache License, Version 2.0 (the "License").
# You may not use this file except in compliance with the License.
# A copy of the License is located at
#     http://www.apache.org/licenses/LICENSE-2.0
# or in the "license" file accompanying this file. This file is distributed
# on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
# express or implied. See the License for the specific language governing
# permissions and limitations under the License.

"""
Helper utils for Model Export tool
"""

import json
import logging
import os
import re
import zipfile
from .model_archiver_error import ModelArchiverError

from .manifest_components.engine import Engine
from .manifest_components.manifest import Manifest
from .manifest_components.model import Model
from .manifest_components.publisher import Publisher

MODEL_ARCHIVE_EXTENSION = '.mar'
TAR_GZ_EXTENSION = '.tar.gz'
MODEL_SERVER_VERSION = '1.0'
MODEL_ARCHIVE_VERSION = '1.0'
MANIFEST_FILE_NAME = 'MANIFEST.json'
MAR_INF = 'MAR-INF'
ONNX_TYPE = '.onnx'


class ModelExportUtils(object):
    """
    Helper utils for Model Archiver tool.
    This class lists out all the methods such as validations for model archiving, ONNX model checking etc.
    """

    @staticmethod
    def get_archive_export_path(export_file_path, model_name, archive_format):
        return os.path.join(export_file_path, '{}{}'.format(model_name,
                                                            MODEL_ARCHIVE_EXTENSION
                                                            if archive_format == "default"
                                                            else TAR_GZ_EXTENSION))

    @staticmethod
    def check_mar_already_exists(model_name, export_file_path, overwrite, archive_format="default"):
        """
        Function to check if .mar already exists
        :param archive_format:
        :param model_name:
        :param export_file_path:
        :param overwrite:
        :return:
        """
        if export_file_path is None:
            export_file_path = os.getcwd()

        export_file = ModelExportUtils.get_archive_export_path(export_file_path, model_name, archive_format)

        if os.path.exists(export_file):
            if overwrite:
                logging.warning("Overwriting %s ...", export_file)
            else:
                raise ModelArchiverError("%s already exists.\n"
                                         "Please specify --force/-f option to overwrite the model archive "
                                         "output file.\n"
                                         "See -h/--help for more details." + export_file)

        return export_file_path

    @staticmethod
    def check_custom_model_types(model_path, model_name=None):
        """
        This functions checks whether any special handling is required for custom model extensions such as
        .onnx, or in the future, for Tensorflow and PyTorch extensions.
        :param model_path:
        :param model_name:
        :return:
        """
        temp_files = []  # List of temp files added to handle custom models
        files_to_exclude = []  # List of files to be excluded from .mar packaging.

        files_set = set(os.listdir(model_path))
        onnx_file = ModelExportUtils.find_unique(files_set, ONNX_TYPE)
        if onnx_file is not None:
            logging.debug("Found ONNX files. Converting ONNX file to model archive...")
            symbol_file, params_file = ModelExportUtils.convert_onnx_model(model_path, onnx_file, model_name)
            files_to_exclude.append(onnx_file)
            temp_files.append(os.path.join(model_path, symbol_file))
            temp_files.append(os.path.join(model_path, params_file))

        # More cases will go here as an if-else block

        return temp_files, files_to_exclude

    @staticmethod
    def find_unique(files, suffix):
        """
        Function to find unique model params file
        :param files:
        :param suffix:
        :return:
        """
        match = [f for f in files if f.endswith(suffix)]
        count = len(match)

        if count == 0:
            return None
        elif count == 1:
            return match[0]
        else:
            raise ModelArchiverError("model-archiver expects only one {} file in the folder."
                                     " Found {} files {} in model-path.".format(suffix, count, match))

    @staticmethod
    def convert_onnx_model(model_path, onnx_file, model_name):
        """
        Util to convert onnx model to MXNet model
        :param model_name:
        :param model_path:
        :param onnx_file:
        :return:
        """
        try:
            import mxnet as mx
            from mxnet.contrib import onnx as onnx_mxnet
        except ImportError:
            raise ModelArchiverError("MXNet package is not installed. Run command: pip install mxnet to install it.")

        try:
            import onnx
        except ImportError:
            raise ModelArchiverError("Onnx package is not installed. Run command: pip install onnx to install it.")

        symbol_file = '%s-symbol.json' % model_name
        params_file = '%s-0000.params' % model_name
        signature_file = 'signature.json'
        # Find input symbol name and shape
        try:
            model_proto = onnx.load(os.path.join(model_path, onnx_file))
        except:
            logging.error("Failed to load the %s model. Verify if the model file is valid", onnx_file)
            raise

        graph = model_proto.graph
        _params = set()
        for tensor_vals in graph.initializer:
            _params.add(tensor_vals.name)

        input_data = []
        for graph_input in graph.input:
            shape = []
            if graph_input.name not in _params:
                for val in graph_input.type.tensor_type.shape.dim:
                    shape.append(val.dim_value)
                input_data.append((graph_input.name, tuple(shape)))

        try:
            sym, arg_params, aux_params = onnx_mxnet.import_model(os.path.join(model_path, onnx_file))
            # UNION of argument and auxillary parameters
            params = dict(arg_params, **aux_params)
        except:
            logging.error("Failed to import %s file to onnx. Verify if the model file is valid", onnx_file)
            raise

        try:
            # rewrite input data_name correctly
            with open(os.path.join(model_path, signature_file), 'r') as f:
                data = json.loads(f.read())
                data['inputs'][0]['data_name'] = input_data[0][0]
                data['inputs'][0]['data_shape'] = [int(i) for i in input_data[0][1]]
            with open(os.path.join(model_path, signature_file), 'w') as f:
                f.write(json.dumps(data, indent=2))

            with open(os.path.join(model_path, symbol_file), 'w') as f:
                f.write(sym.tojson())
        except:
            logging.error("Failed to write the signature or symbol files for %s model", onnx_file)
            raise

        save_dict = {('arg:%s' % k): v.as_in_context(mx.cpu()) for k, v in params.items()}
        mx.nd.save(os.path.join(model_path, params_file), save_dict)
        return symbol_file, params_file

    @staticmethod
    def generate_publisher(publisherargs):
        publisher = Publisher(author=publisherargs.author, email=publisherargs.email)
        return publisher

    @staticmethod
    def generate_engine(engineargs):
        engine = Engine(engine_name=engineargs.engine)
        return engine

    @staticmethod
    def generate_model(modelargs):
        model = Model(model_name=modelargs.model_name, handler=modelargs.handler)
        return model

    @staticmethod
    def generate_manifest_json(args):
        """
        Function to generate manifest as a json string from the inputs provided by the user in the command line
        :param args:
        :return:
        """
        arg_dict = vars(args)

        publisher = ModelExportUtils.generate_publisher(args) if 'author' in arg_dict and 'email' in arg_dict else None

        engine = ModelExportUtils.generate_engine(args) if 'engine' in arg_dict else None

        model = ModelExportUtils.generate_model(args)

        manifest = Manifest(runtime=args.runtime, model=model, engine=engine, publisher=publisher)

        return str(manifest)

    @staticmethod
    def clean_temp_files(temp_files):
        for f in temp_files:
            os.remove(f)

    @staticmethod
    def archive(export_file, model_name, model_path, files_to_exclude, manifest, archive_format="default"):
        """
        Create a model-archive
        :param archive_format:
        :param export_file:
        :param model_name:
        :param model_path:
        :param files_to_exclude:
        :param manifest:
        :return:
        """
        mar_path = ModelExportUtils.get_archive_export_path(export_file, model_name, archive_format)
        try:
            if archive_format == "default":
                with zipfile.ZipFile(mar_path, 'w', zipfile.ZIP_DEFLATED) as z:
                    ModelExportUtils.archive_dir(model_path, z, set(files_to_exclude), archive_format, model_name)
                    # Write the manifest here now as a json
                    z.writestr(os.path.join(MAR_INF, MANIFEST_FILE_NAME), manifest)
            elif archive_format == "tgz":
                import tarfile
                from io import BytesIO
                with tarfile.open(mar_path, 'w:gz') as z:
                    ModelExportUtils.archive_dir(model_path, z, set(files_to_exclude), archive_format, model_name)
                    # Write the manifest here now as a json
                    tar_manifest = tarfile.TarInfo(name=os.path.join(model_name, MAR_INF, MANIFEST_FILE_NAME))
                    tar_manifest.size = len(manifest.encode('utf-8'))
                    z.addfile(tarinfo=tar_manifest, fileobj=BytesIO(manifest.encode()))
                    z.close()
            else:
                logging.error("Unknown format %s", archive_format)

        except IOError:
            logging.error("Failed to save the model-archive to model-path \"%s\". "
                          "Check the file permissions and retry.", export_file)
            raise
        except:
            logging.error("Failed to convert %s to the model-archive.", model_name)
            raise

    @staticmethod
    def archive_dir(path, dst, files_to_exclude, archive_format, model_name):

        """
        This method zips the dir and filters out some files based on a expression
        :param archive_format:
        :param path:
        :param dst:
        :param model_name:
        :param files_to_exclude:
        :return:
        """
        unwanted_dirs = {'__MACOSX', '__pycache__'}

        for root, directories, files in os.walk(path):
            # Filter directories
            directories[:] = [d for d in directories if ModelExportUtils.directory_filter(d, unwanted_dirs)]
            # Filter files
            files[:] = [f for f in files if ModelExportUtils.file_filter(f, files_to_exclude)]
            for f in files:
                file_path = os.path.join(root, f)
                if archive_format == "tgz":
                    dst.add(file_path, arcname=os.path.join(model_name, os.path.relpath(file_path, path)))
                else:
                    dst.write(file_path, os.path.relpath(file_path, path))

    @staticmethod
    def directory_filter(directory, unwanted_dirs):
        """
        This method weeds out unwanted hidden directories from the model archive .mar file
        :param directory:
        :param unwanted_dirs:
        :return:
        """
        if directory in unwanted_dirs:
            return False
        if directory.startswith('.'):
            return False

        return True

    @staticmethod
    def file_filter(current_file, files_to_exclude):
        """
        This method weeds out unwanted files
        :param current_file:
        :param files_to_exclude:
        :return:
        """
        files_to_exclude.add('MANIFEST.json')
        if current_file in files_to_exclude:
            return False

        elif current_file.endswith(('.pyc', '.DS_Store', '.mar')):
            return False

        return True

    @staticmethod
    def check_model_name_regex_or_exit(model_name):
        """
        Method checks whether model name passes regex filter.
        If the regex Filter fails, the method exits.
        :param model_name:
        :return:
        """
        if not re.match(r'^[A-Za-z0-9][A-Za-z0-9_\-.]*$', model_name):
            raise ModelArchiverError("Model name contains special characters.\n"
                                     "The allowed regular expression filter for model "
                                     "name is: ^[A-Za-z0-9][A-Za-z0-9_\\-.]*$")

    @staticmethod
    def validate_inputs(model_path, model_name, export_path):
        ModelExportUtils.check_model_name_regex_or_exit(model_name)
        if not os.path.isdir(os.path.abspath(export_path)):
            raise ModelArchiverError("Given export-path {} is not a directory. "
                                     "Point to a valid export-path directory.".format(export_path))

        if not os.path.isdir(os.path.abspath(model_path)):
            raise ModelArchiverError("Given model-path {} is not a valid directory. "
                                     "Point to a valid model-path directory.".format(model_path))
