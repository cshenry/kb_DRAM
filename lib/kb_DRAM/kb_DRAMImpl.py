# -*- coding: utf-8 -*-
#BEGIN_HEADER
import logging
import os
import pandas as pd
import yaml
import warnings

from mag_annotator import __version__ as dram_version
from mag_annotator.database_handler import import_config, set_database_paths, print_database_locations
from mag_annotator.annotate_bins import annotate_bins, annotate_called_genes
from mag_annotator.summarize_genomes import summarize_genomes
from mag_annotator.annotate_vgfs import annotate_vgfs, remove_bad_chars
from mag_annotator.summarize_vgfs import summarize_vgfs
from mag_annotator.utils import remove_suffix

from installed_clients.WorkspaceClient import Workspace as workspaceService
from installed_clients.AssemblyUtilClient import AssemblyUtil
from installed_clients.GenomeFileUtilClient import GenomeFileUtil
from installed_clients.cb_annotation_ontology_apiClient import cb_annotation_ontology_api
from installed_clients.KBaseDataObjectToFileUtilsClient import KBaseDataObjectToFileUtils
from installed_clients.DataFileUtilClient import DataFileUtil

from .utils.dram_util import get_annotation_files, get_distill_files, generate_genomes, add_ontology_terms,\
    get_viral_distill_files
from .utils.kbase_util import generate_product_report

THREADS = 30

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
    VERSION = ""
    GIT_URL = ""
    GIT_COMMIT_HASH = ""

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
        if not isinstance(params['output_name'], str) or not len(params['output_name']):
            raise ValueError('Pass in a valid genomeSet output name')
        # if not isinstance(params['assembly_output_ref'], str) or not len(params['assembly_output_ref']):
        #     raise ValueError('Pass in a valid assembly reference string')
        if not isinstance(params['desc'], str) or not len(params['desc']):
            raise ValueError('Pass in a valid genomeSet description')
        if not isinstance(params['min_contig_size'], int) or (params['min_contig_size'] < 0):
            raise ValueError('Min contig size must be a non-negative integer')

        # setup params
        with open("/kb/module/kbase.yml", 'r') as stream:
            data_loaded = yaml.safe_load(stream)
        version = str(data_loaded['module-version'])

        is_metagenome = params['is_metagenome']
        min_contig_size = params['min_contig_size']
        trans_table = str(params['trans_table'])
        bitscore = params['bitscore']
        rbh_bitscore = params['rbh_bitscore']
        output_dir = os.path.join(self.shared_folder, 'DRAM_annos')
        count = 0
        while os.path.exists(output_dir):
           count += 1
           output_dir = os.path.join(self.shared_folder, 'DRAM_annos' + f'_{count}')

        output_objects = []

        # create Util objects
        wsClient = workspaceService(self.workspaceURL, token=ctx['token'])
        assembly_util = AssemblyUtil(self.callback_url)
        genome_util = GenomeFileUtil(self.callback_url)

        # set DRAM database locations
        print('DRAM version: %s' % dram_version)
        import_config('/data/DRAM_databases/CONFIG')
        # This is a hack to get around a bug in my database setup
        set_database_paths(description_db_loc='/data/DRAM_databases/description_db.sqlite')
        print_database_locations()

        # get files
        assemblies = assembly_util.get_fastas({'ref_lst': [params['assembly_input_ref']]})
        # would paths ever have more than one thing?
        fasta_locs = [assembly_data['paths'][0] for assembly_ref, assembly_data in assemblies.items()]
        # get assembly refs from dram assigned genome names
        assembly_ref_dict = {os.path.splitext(os.path.basename(remove_suffix(assembly_data['paths'][0], '.gz')))[0]:
                             assembly_ref for assembly_ref, assembly_data in assemblies.items()}

        # annotate and distill with DRAM
        annotate_bins(fasta_locs, output_dir, min_contig_size, trans_table=trans_table, bit_score_threshold=bitscore,
                      rbh_bit_score_threshold=rbh_bitscore, low_mem_mode=True, rename_bins=False, keep_tmp_dir=False,
                      threads=THREADS, verbose=False)
        output_files = get_annotation_files(output_dir)
        distill_output_dir = os.path.join(output_dir, 'distilled')
        summarize_genomes(output_files['annotations']['path'], output_files['trnas']['path'],
                          output_files['rrnas']['path'], output_dir=distill_output_dir, groupby_column='fasta')
        output_files = get_distill_files(distill_output_dir, output_files)

        if is_metagenome:
            pass
        else: # TODO add put this in a function
            # generate genome files
            annotations = pd.read_csv(output_files['annotations']['path'], sep='\t', index_col=0)
            genome_objects = generate_genomes(annotations, output_files['genes_fna']['path'],
                                              output_files['genes_faa']['path'], assembly_ref_dict, assemblies,
                                              params["workspace_name"], ctx.provenance())
            if len(genome_objects) == 1:
                pass #TODO, just return a kbase genome
            genome_ref_dict = dict()
            genome_set_elements = dict()
            for genome_object in genome_objects:
                info = genome_util.save_one_genome(genome_object)["info"]
                genome_ref = '%s/%s/%s' % (info[6], info[0], info[4])
                genome_set_elements[genome_object["name"]] = {'ref': genome_ref}
                output_objects.append({"ref": genome_ref,
                                       "description": 'Annotated Genome'})
                genome_ref_dict[genome_object["name"]] = genome_ref

            # add ontology terms
            anno_api = cb_annotation_ontology_api(self.callback_url)
            
            ontology_events = add_ontology_terms(annotations, params['desc'], version, params['workspace_name'],
                                                 self.workspaceURL, genome_ref_dict)
            [anno_api.add_annotation_ontology_events(i) for i in ontology_events]

            # make genome set
            # TODO: only make genome set if there is more than one genome
            if 'provenance' in ctx:
                provenance = ctx['provenance']
            else:
                provenance = [{}]
            # add additional info to provenance here, in this case the input data object reference
            provenance[0]['input_ws_objects'] = list(genome_ref_dict.values())
            provenance[0]['service'] = 'kb_SetUtilities'
            provenance[0]['method'] = 'KButil_Batch_Create_GenomeSet'
            output_genomeSet_obj = {'description': params['desc'],
                                    'elements': genome_set_elements}
            if len(params['output_name']):
                output_genomeSet_name = params['output_name']
            else:
                output_genomeSet_name = params['assembly_input_ref'] + "_DRAM"
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
        if not isinstance(params['genome_input_ref'], str) or not len(params['genome_input_ref']):
            raise ValueError('Pass in a valid genome reference string')

        # setup
        with open("/kb/module/kbase.yml", 'r') as stream:
            data_loaded = yaml.safe_load(stream)
        version = str(data_loaded['module-version'])
        genome_input_ref = params['genome_input_ref']
        bitscore = params['bitscore']
        rbh_bitscore = params['rbh_bitscore']

        # create Util objects
        wsClient = workspaceService(self.workspaceURL, token=ctx['token'])
        object_to_file_utils = KBaseDataObjectToFileUtils(self.callback_url, token=ctx['token'])

        # set DRAM database locations
        print('DRAM version: %s' % dram_version)
        import_config('/data/DRAM_databases/CONFIG')
        # This is a hack to get around a bug in my database setup
        set_database_paths(description_db_loc='/data/DRAM_databases/description_db.sqlite')
        print_database_locations()

        # get genomes
        genome_dir = os.path.join(self.shared_folder, 'genomes')
        os.mkdir(genome_dir)
        genome_info = wsClient.get_object_info_new({'objects': [{'ref': genome_input_ref}]})[0]
        genome_input_type = genome_info[2]
        faa_locs = list()
        genome_ref_dict = {}
        if 'GenomeSet' in genome_input_type:
            genomeSet_object = wsClient.get_objects2({'objects': [{'ref': genome_input_ref}]})['data'][0]['data']
            for ref_dict in genomeSet_object['elements'].values():
                genome_ref = ref_dict['ref']
                name = wsClient.get_object_info_new({'objects': [{'ref': genome_ref}]})[0][1]
                genome_ref_dict[name] = genome_ref
        else:
            genome_ref_dict[genome_info[1]] = genome_input_ref
        for genome_name, genome_ref in genome_ref_dict.items():
            # this makes the names match if you are doing a genome or genomeSet
            faa_file = '%s.faa' % genome_name
            faa_object = object_to_file_utils.GenomeToFASTA({
                "genome_ref": genome_ref,
                "file": faa_file,
                "dir": genome_dir,
                "console": [],
                "invalid_msgs": [],
                'residue_type': 'protein',
                'feature_type': 'CDS',
                'record_id_pattern': '%%feature_id%%',
                'record_desc_pattern': '[%%genome_id%%]',
                'case': 'upper',
                'linewrap': 50
            })
            faa_locs.append(faa_object['fasta_file_path'])

        # annotate and distill with DRAM
        output_dir = os.path.join(self.shared_folder, 'DRAM_annos')
        annotate_called_genes(faa_locs, output_dir, bit_score_threshold=bitscore, rbh_bit_score_threshold=rbh_bitscore,
                              low_mem_mode=True, rename_genes=False, keep_tmp_dir=False, threads=THREADS, verbose=False)
        output_files = get_annotation_files(output_dir)
        distill_output_dir = os.path.join(output_dir, 'distilled')
        summarize_genomes(output_files['annotations']['path'], output_files['trnas']['path'],
                          output_files['rrnas']['path'], output_dir=distill_output_dir, groupby_column='fasta')
        output_files = get_distill_files(distill_output_dir, output_files)

        # add ontology terms
        annotations = pd.read_csv(output_files['annotations']['path'], sep='\t', index_col=0, dtype={'fasta': str})
        anno_api = cb_annotation_ontology_api(self.callback_url)
        ontology_events = add_ontology_terms(annotations, "DRAM genome annotated", version, params['workspace_name'],
                                             self.workspaceURL, genome_ref_dict)
        annotation_events = [anno_api.add_annotation_ontology_events(i) for i in ontology_events]

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

    def run_kb_dramv_annotate(self, ctx, params):
        """
        :param params: instance of mapping from String to unspecified object
        :returns: instance of type "ReportResults" -> structure: parameter
           "report_name" of String, parameter "report_ref" of String
        """
        # ctx is the context object
        # return variables are: output
        #BEGIN run_kb_dramv_annotate
        # validate inputs
        if not isinstance(params['assembly_input_ref'], str) or not len(params['assembly_input_ref']):
            raise ValueError('Pass in a valid assembly reference string')
        # this may not be apropriate
        if not isinstance(params['output_name'], str):
            raise ValueError('Pass in a valid genomeSet output name')
        if not isinstance(params['min_contig_size'], int) or (params['min_contig_size'] < 0):
            raise ValueError('Min contig size must be a non-negative integer')
        warnings.filterwarnings("ignore")


        # setup
        affi_contigs_shock_ids = params['affi_contigs_shock_id']
        min_contig_size = params['min_contig_size']
        trans_table = str(params['trans_table'])
        bitscore = params['bitscore']
        rbh_bitscore = params['rbh_bitscore']

        assembly_util = AssemblyUtil(self.callback_url)
        datafile_util = DataFileUtil(self.callback_url)

        # get contigs and merge
        assemblies = assembly_util.get_fastas({'ref_lst': [params['assembly_input_ref']]})
        fasta = os.path.join(self.shared_folder, 'merged_contigs.fasta')
        with open(fasta, 'w') as f:
            for assembly_ref, assembly_data in assemblies.items():
                fasta_path = assembly_data['paths'][0]
                for line in open(fasta_path):
                    f.write(line)

        # get affi contigs, read all and merge
        affi_contigs_path = os.path.join(self.shared_folder, 'VIRSorter_affi-contigs.tab')
        with open(affi_contigs_path, 'w') as f:
            for affi_contigs_shock_id in affi_contigs_shock_ids:
                temp_affi_contigs_path = os.path.join(self.shared_folder, 'temp_VIRSorter_affi-contigs.tab')
                temp_affi_contigs = datafile_util.shock_to_file({
                    'shock_id': affi_contigs_shock_id,
                    'file_path': temp_affi_contigs_path,
                    'unpack': 'unpack'
                })['file_path']
                for line in open(temp_affi_contigs):
                    f.write(line)
                os.remove(temp_affi_contigs)

        # set DRAM database locations
        print('DRAM version: %s' % dram_version)
        import_config('/data/DRAM_databases/CONFIG')
        # This is a hack to get around a bug in my database setup
        set_database_paths(description_db_loc='/data/DRAM_databases/description_db.sqlite')
        print_database_locations()

        # clean affi contigs file
        cleaned_fasta = os.path.join(self.shared_folder, '%s.cleaned.fasta' % os.path.basename(fasta))
        remove_bad_chars(input_fasta=fasta, output=cleaned_fasta)
        cleaned_affi_contigs = os.path.join(self.shared_folder, 'VIRSorter_affi-contigs.cleaned.tab')
        remove_bad_chars(input_virsorter_affi_contigs=affi_contigs_path, output=cleaned_affi_contigs)

        # annotate and distill
        output_dir = os.path.join(self.shared_folder, 'DRAM_annos')
        annotate_vgfs(cleaned_fasta, cleaned_affi_contigs, output_dir, min_contig_size, trans_table=trans_table,
                      bit_score_threshold=bitscore, rbh_bit_score_threshold=rbh_bitscore, low_mem_mode=True,
                      keep_tmp_dir=False, threads=THREADS, verbose=False)
        output_files = get_annotation_files(output_dir)
        distill_output_dir = os.path.join(output_dir, 'distilled')
        summarize_vgfs(output_files['annotations']['path'], distill_output_dir, groupby_column='scaffold')
        output_files = get_viral_distill_files(distill_output_dir, output_files)

        # generate report
        product_html_loc = os.path.join(distill_output_dir, 'product.html')
        report = generate_product_report(self.callback_url, params['workspace_name'], output_dir,
                                         product_html_loc, output_files)
        output = {
            'report_name': report['name'],
            'report_ref': report['ref'],
        }
        #END run_kb_dramv_annotate

        # At some point might do deeper type checking...
        if not isinstance(output, dict):
            raise ValueError('Method run_kb_dramv_annotate return value ' +
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
