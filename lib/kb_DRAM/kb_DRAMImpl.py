# -*- coding: utf-8 -*-
#BEGIN_HEADER
import logging
import os
import pandas as pd
import yaml

from mag_annotator import __version__ as dram_version
from mag_annotator.database_processing import import_config, set_database_paths, print_database_locations
from mag_annotator.annotate_bins import annotate_bins, annotate_called_genes
from mag_annotator.summarize_genomes import summarize_genomes
from mag_annotator.utils import remove_suffix

from installed_clients.WorkspaceClient import Workspace as workspaceService
from installed_clients.AssemblyUtilClient import AssemblyUtil
from installed_clients.GenomeFileUtilClient import GenomeFileUtil
from installed_clients.annotation_ontology_apiServiceClient import annotation_ontology_api
from installed_clients.KBaseDataObjectToFileUtilsClient import KBaseDataObjectToFileUtils

from .utils.dram_util import get_annotation_files, get_distill_files, generate_genomes, add_ontology_terms
from .utils.kbase_util import generate_product_report

# TODO: Fix no pfam annotations bug
#END_HEADER


class kb_DRAM:
    '''
    Module Name:
    kb_DRAM

    Module Description:
    A KBase module: kb_DRAM
    '''

    ######## WARNING FOR GEVENT USERS ####### noqa
    # Since asynchronous IO can lead to methods - even the same method -
    # interrupting each other, you must be *very* careful when using global
    # state. A method could easily clobber the state set by another while
    # the latter method is running.
    ######################################### noqa
    VERSION = "0.0.2"
    GIT_URL = "https://github.com/shafferm/kb_DRAM.git"
    GIT_COMMIT_HASH = "d5809a98f2a855e61e51621e7292b4d0e5f55a46"

    #BEGIN_CLASS_HEADER
    #END_CLASS_HEADER

    # config contains contents of config file in a hash or None if it couldn't
    # be found
    def __init__(self, config):
        #BEGIN_CONSTRUCTOR
        self.callback_url = os.environ['SDK_CALLBACK_URL']
        self.workspaceURL = config['workspace-url']
        self.shared_folder = config['scratch']
        logging.basicConfig(format='%(created)s %(levelname)s: %(message)s',
                            level=logging.INFO)
        #END_CONSTRUCTOR
        pass


    def run_kb_dram_annotate(self, ctx, params):
        """
        This example function accepts any number of parameters and returns results in a KBaseReport
        :param params: instance of mapping from String to unspecified object
        :returns: instance of type "ReportResults" -> structure: parameter
           "report_name" of String, parameter "report_ref" of String
        """
        # ctx is the context object
        # return variables are: output
        #BEGIN run_kb_dram_annotate
        # validate inputs
        if not isinstance(params['assembly_input_ref'], str) or not len(params['assembly_input_ref']):
            raise ValueError('Pass in a valid assembly reference string')
        if not isinstance(params['desc'], str) or not len(params['desc']):
            raise ValueError('Pass in a valid genomeSet description')
        if not isinstance(params['output_name'], str) or not len(params['output_name']):
            raise ValueError('Pass in a valid genomeSet output name')
        if not isinstance(params['min_contig_size'], int) or (params['min_contig_size'] < 0):
            raise ValueError('Min contig size must be a non-negative integer')

        # setup params
        with open("/kb/module/kbase.yml", 'r') as stream:
            data_loaded = yaml.load(stream)
        version = str(data_loaded['module-version'])
        min_contig_size = params['min_contig_size']
        output_dir = os.path.join(self.shared_folder, 'DRAM_annos')
        output_objects = []

        # create Util objects
        wsClient = workspaceService(self.workspaceURL, token=ctx['token'])
        assembly_util = AssemblyUtil(self.callback_url)
        genome_util = GenomeFileUtil(self.callback_url)

        # set DRAM database locations
        print(dram_version)
        import_config('/data/DRAM_databases/CONFIG')
        # This is a hack to get around a bug in my database setup
        set_database_paths(description_db_loc='/data/DRAM_databases/description_db.sqlite')
        print_database_locations()

        # get files
        assemblies = assembly_util.get_fastas({'ref_lst': [params['assembly_input_ref']]})
        # would paths ever have more than one thing?
        fasta_locs = [assembly_data['paths'][0] for assembly_ref, assembly_data in assemblies.items()]
        # get assembly refs from dram assigned genome names
        # TODO: rewrite annotate_bins to take optional list of fasta names and pass assembly refs as list
        # TODO: something to get rid of the very ugly and confusing assembly_ref_dict
        assembly_ref_dict = {os.path.splitext(os.path.basename(remove_suffix(assembly_data['paths'][0], '.gz')))[0]:
                             assembly_ref for assembly_ref, assembly_data in assemblies.items()}

        # annotate and distill with DRAM
        annotate_bins(fasta_locs, output_dir, min_contig_size, low_mem_mode=True, rename_bins=False, keep_tmp_dir=False,
                      threads=4, verbose=False)
        output_files = get_annotation_files(output_dir)
        distill_output_dir = os.path.join(output_dir, 'distilled')
        print(os.listdir(output_dir))
        summarize_genomes(output_files['annotations']['path'], output_files['trnas']['path'],
                          output_files['rrnas']['path'], output_dir=distill_output_dir, groupby_column='fasta')
        output_files = get_distill_files(distill_output_dir, output_files)

        # generate genome files
        annotations = pd.read_csv(output_files['annotations']['path'], sep='\t', index_col=0)
        genome_objects = generate_genomes(annotations, output_files['genes_fna']['path'],
                                          output_files['genes_faa']['path'], assembly_ref_dict, assemblies,
                                          params["workspace_name"], ctx.provenance())

        genome_ref_dict = dict()
        genome_set_elements = dict()
        for genome_object in genome_objects:
            info = genome_util.save_one_genome(genome_object)["info"]
            genome_ref = '%s/%s/%s' % (info[6], info[0], info[4])
            genome_object_name = genome_object["name"]
            genome_set_elements[genome_object_name] = dict()
            genome_set_elements[genome_object_name]['ref'] = genome_ref
            output_objects.append({"ref": genome_ref,
                                   "description": 'Annotated Genome'})
            genome_ref_dict[genome_object_name] = genome_ref

        # add ontology terms
        anno_api = annotation_ontology_api(service_ver="beta")
        ontology_events = add_ontology_terms(annotations, params['desc'], version, params['workspace_name'],
                                             self.workspaceURL, genome_ref_dict)
        [anno_api.add_annotation_ontology_events(i) for i in ontology_events]

        # make genome set
        if 'provenance' in ctx:
            provenance = ctx['provenance']
        else:
            provenance = [{}]
        # add additional info to provenance here, in this case the input data object reference
        provenance[0]['input_ws_objects'] = []
        for ass_ref in genome_ref_dict.values():
            provenance[0]['input_ws_objects'].append(ass_ref)
        provenance[0]['service'] = 'kb_SetUtilities'
        provenance[0]['method'] = 'KButil_Batch_Create_GenomeSet'
        output_genomeSet_obj = {'description': params['desc'],
                                'elements': genome_set_elements
                                }
        output_genomeSet_name = params['output_name']
        new_obj_info = wsClient.save_objects({'workspace': params['workspace_name'],
                                              'objects': [{'type': 'KBaseSearch.GenomeSet',
                                                           'data': output_genomeSet_obj,
                                                           'name': output_genomeSet_name,
                                                           'meta': {},
                                                           'provenance': provenance
                                                           }]
                                              })[0]
        genome_set_ref = '%s/%s/%s' % (new_obj_info[6], new_obj_info[0], new_obj_info[4])
        output_objects.append({"ref": genome_set_ref,
                               "description": params['desc']})

        # generate report
        product_html_loc = os.path.join(distill_output_dir, 'product.html')
        report = generate_product_report(self.callback_url, params['workspace_name'], output_dir, product_html_loc,
                                         output_files, output_objects)
        output = {
            'report_name': report['name'],
            'report_ref': report['ref'],
        }
        #END run_kb_dram_annotate

        # At some point might do deeper type checking...
        if not isinstance(output, dict):
            raise ValueError('Method run_kb_dram_annotate return value ' +
                             'output is not type dict as required.')
        # return the results
        return [output]

    def run_kb_dram_annotate_genome(self, ctx, params):
        """
        :param params: instance of mapping from String to unspecified object
        :returns: instance of type "ReportResults" -> structure: parameter
           "report_name" of String, parameter "report_ref" of String
        """
        # ctx is the context object
        # return variables are: output
        #BEGIN run_kb_dram_annotate_genome
        # validate inputs
        if not isinstance(params['genome_input_ref'], str) or not len(params['genome_input_ref']):
            raise ValueError('Pass in a valid genome reference string')

        # setup
        with open("/kb/module/kbase.yml", 'r') as stream:
            data_loaded = yaml.load(stream)
        version = str(data_loaded['module-version'])
        genome_input_ref = params['genome_input_ref']
        output_dir = os.path.join(self.shared_folder, 'DRAM_annos')

        # create Util objects
        wsClient = workspaceService(self.workspaceURL, token=ctx['token'])
        object_to_file_utils = KBaseDataObjectToFileUtils(self.callback_url)

        # set DRAM database locations
        print(dram_version)
        import_config('/data/DRAM_databases/CONFIG')
        # This is a hack to get around a bug in my database setup
        set_database_paths(description_db_loc='/data/DRAM_databases/description_db.sqlite')
        print_database_locations()

        # get genomes
        genome_input_type = wsClient.get_object_info_new({'objects': [{'ref': genome_input_ref}]})[0][2]
        if 'GenomeSet' in genome_input_type:
            faa_objects = object_to_file_utils.GenomeSetToFASTA({"genomeSet_ref": genome_input_ref,
                                                                 "file": 'DRAM',
                                                                 "residue_type": 'P',
                                                                 "merge_fasta_files": False})
            # DRAM needs a fasta file ending so need to move to add ending
            faa_locs = list()
            genome_ref_dict = {}
            for fasta_path in faa_objects['fasta_file_path_list']:
                new_path = '%s.faa' % fasta_path
                os.rename(fasta_path, new_path)
                faa_locs.append(new_path)
                file_name = os.path.splitext(os.path.basename(remove_suffix(new_path, '.gz')))[0]
                genome_ref = file_name.split('.')[1].replace('-', '/')
                genome_ref_dict[file_name] = genome_ref
        else:
            # this makes the names match if you are doing a genome or genomeSet
            faa_file = 'DRAM.%s.faa' % genome_input_ref.replace('/', '-')
            faa_locs = object_to_file_utils.GenomeToFASTA({"genome_ref": genome_input_ref,
                                                           "file": faa_file,
                                                           "residue_type": 'P'})
            genome_ref_dict = {genome_input_ref: faa_file}

        # annotate and distill with DRAM
        annotate_called_genes(faa_locs, output_dir, low_mem_mode=True, keep_tmp_dir=False, threads=4, verbose=False)
        output_files = get_annotation_files(output_dir)
        distill_output_dir = os.path.join(output_dir, 'distilled')
        summarize_genomes(output_files['annotations']['path'], output_files['trnas']['path'],
                          output_files['rrnas']['path'], output_dir=distill_output_dir, groupby_column='fasta')
        output_files = get_distill_files(distill_output_dir, output_files)

        # add ontology terms
        annotations = pd.read_csv(output_files['annotations']['path'], sep='\t', index_col=0)
        anno_api = annotation_ontology_api(service_ver="beta")
        ontology_events = add_ontology_terms(annotations, params['desc'], version, params['workspace_name'],
                                             self.workspaceURL, genome_ref_dict)
        [anno_api.add_annotation_ontology_events(i) for i in ontology_events]

        # generate report
        product_html_loc = os.path.join(distill_output_dir, 'product.html')
        report = generate_product_report(self.callback_url, params['workspace_name'], output_dir, product_html_loc,
                                         output_files)
        output = {
            'report_name': report['name'],
            'report_ref': report['ref'],
        }
        #END run_kb_dram_annotate_genome

        # At some point might do deeper type checking...
        if not isinstance(output, dict):
            raise ValueError('Method run_kb_dram_annotate_genome return value ' +
                             'output is not type dict as required.')
        # return the results
        return [output]
    def status(self, ctx):
        #BEGIN_STATUS
        returnVal = {'state': "OK",
                     'message': "",
                     'version': self.VERSION,
                     'git_url': self.GIT_URL,
                     'git_commit_hash': self.GIT_COMMIT_HASH}
        #END_STATUS
        return [returnVal]
