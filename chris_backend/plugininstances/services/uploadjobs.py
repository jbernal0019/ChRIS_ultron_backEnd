"""
Upload job manager module that provides the interface for submitting and
checking the execution status of upload jobs running in a remote compute 
environment (ChRIS / pfcon interface) as well as deleting them when finished.
"""

import logging
import json

from pfconclient.client import JobType
from pfconclient.exceptions import PfconRequestException

from django.utils import timezone

from core.utils import json_zip2str
from plugininstances.models import PluginInstance
from .abstractjobs import PluginInstanceJob


logger = logging.getLogger(__name__)


class PluginInstanceUploadJob(PluginInstanceJob):
    """
    ``PluginInstanceUploadJob`` provides a concrete implementation for managing a remote 
    upload job related to a plugin instance.
    """

    def run(self):
        """
        Run the plugin instance upload job via a call to a remote pfcon service.
        """
        if self.c_plugin_inst.status == 'cancelled':
            return

        job_id = self.str_job_id        

        # create job description dictionary
        job_descriptors = {
            'job_output_path': self.c_plugin_inst.get_output_path()
        }

        pfcon_url = self.pfcon_client.url

        logger.info(f'Submitting upload job {job_id} to pfcon url -->{pfcon_url}<--, '
                    f'description: {json.dumps(job_descriptors, indent=4)}')
        try:
            d_resp = self._submit(JobType.UPLOAD, job_id, job_descriptors)
        except PfconRequestException as e:
            logger.error(f'[CODE01,{job_id}]: Error submitting upload job to pfcon url '
                         f'-->{pfcon_url}<--, detail: {str(e)}')
            
            self.c_plugin_inst.error_code = 'CODE01'
            self.c_plugin_inst.status = 'cancelled'  # giving up
            self.save_plugin_instance_final_status()
        else:
            logger.info(f'Successfully submitted upload job {job_id} to pfcon url '
                        f'-->{pfcon_url}<--, response: {json.dumps(d_resp, indent=4)}')
            
            # update the job status and summary
            self.c_plugin_inst.summary = self.get_job_status_summary(d_resp)
            self.c_plugin_inst.raw = json_zip2str(d_resp)

            # https://github.com/FNNDSC/ChRIS_ultron_backEnd/issues/408
            now = timezone.now()
            self.c_plugin_inst.start_date = now
            self.c_plugin_inst.end_date = now
            self.c_plugin_inst.save()

    def check_exec_status(self):
        """
        Check a plugin instance upload job's execution status. If the associated job's  
        execution time exceeds the maximum set for the remote compute environment then 
        the job is cancelled. Otherwise the job's execution status is fetched from the 
        remote.
        """
        if self.c_plugin_inst.status == 'registeringFiles':

            if self.c_plugin_inst.summary['pullPath']['status']:
                return self.c_plugin_inst.status
            
            job_id = self.str_job_id

            if self._job_has_timeout():
                logger.error(f'[CODE13,{job_id}]: Error, upload job exceeded maximum '
                             f'execution time')
                self.c_plugin_inst.error_code = 'CODE13'
                self.cancel_exec()
                return self.c_plugin_inst.status

            pfcon_url = self.pfcon_client.url
            logger.info(f'Sending job status request to pfcon url -->{pfcon_url}<-- for '
                        f'upload job {job_id}')
            try:
                d_resp = self._get_status(JobType.UPLOAD, job_id)
            except PfconRequestException as e:
                logger.error(f'[CODE02,{job_id}]: Error getting upload job status at pfcon '
                             f'url -->{pfcon_url}<--, detail: {str(e)}')
                return self.c_plugin_inst.status  # return, CUBE will retry later

            logger.info(f'Successful job status response from pfcon url -->{pfcon_url}<--'
                        f' for upload job {job_id}: {json.dumps(d_resp, indent=4)}')
            
            status = d_resp['compute']['status']
            logger.info(f'Current upload job {job_id} remote status = {status}')
            logger.info(f'Current upload job {job_id} plugin instance DB status = '
                        f'{self.c_plugin_inst.status}')

            summary = self.get_job_status_summary(d_resp)
            self.c_plugin_inst.summary = summary
            raw = json_zip2str(d_resp)
            self.c_plugin_inst.raw = raw

            # only update (atomically) if status='started' to avoid concurrency problems
            PluginInstance.objects.filter(
                id=self.c_plugin_inst.id,
                status='started').update(summary=summary, raw=raw)

            if status == 'finishedSuccessfully':
                self.handle_finished_successfully_status()
            elif status == 'finishedWithError':
                self.handle_finished_with_error_status()
            elif status == 'undefined':
                self.handle_undefined_status()
        return self.c_plugin_inst.status

    def cancel_exec(self):
        """
        Cancel a plugin instance upload job execution. It connects to the remote service
        to cancel job.
        """
        self.c_plugin_inst.status = 'cancelled'
        self.save_plugin_instance_final_status()
        # TODO: schedule delete job in case there is data in the remote compute 
        # to be deleted and then that job should schedule delete for this upload job

    def delete(self):
        """
        Delete a plugin instance upload job from the remote compute. It connects to the
        remote service to delete the job.
        """
        pfcon_url = self.pfcon_client.url
        job_id = self.str_job_id
        logger.info(f'Deleting upload job {job_id} from pfcon at url '
                    f'-->{pfcon_url}<--')
        try:
            self._delete(JobType.UPLOAD, job_id)
        except PfconRequestException as e:
            logger.error(f'[CODE12,{job_id}]: Error deleting upload job from '
                             f'pfcon at url -->{pfcon_url}<--, detail: {str(e)}')
            self.c_plugin_inst.error_code = 'CODE12'
        else:
            logger.info(f'Successfully deleted upload job {job_id} from pfcon at '
                        f'url -->{pfcon_url}<--')
            if self.c_plugin_inst.error_code == 'CODE12':
                self.c_plugin_inst.error_code = ''

    def handle_finished_successfully_status(self):
        """
        Handle the 'finishedSuccessfully' status returned by the remote compute.
        """
        job_id = self.str_job_id
        logger.info(f'Successfully finished plugin instance upload job {job_id}')
        
        # data successfully uploaded so update instance summary
        self.c_plugin_inst.summary['pullPath']['status'] = True
        self.c_plugin_inst.save(update_fields=['summary'])
        # TODO: register files with the DB

    def handle_finished_with_error_status(self):
        """
        Handle the 'finishedWithError' status returned by the remote compute.
        """
        job_id = self.str_job_id
        self.c_plugin_inst.status = 'cancelled'
        logger.error(f'[CODE18,{job_id}]: Error while running upload job, remote compute ' 
                     f'returned finishedWithError status for job {job_id}')
        self.c_plugin_inst.error_code = 'CODE18'
        self.save_plugin_instance_final_status()
        # TODO: schedule delete job in case there is data in the remote compute 
        # to be deleted and then that job should schedule delete for this upload job

    def handle_undefined_status(self):
        """
        Handle the 'undefined' status returned by the remote compute.
        """
        job_id = self.str_job_id
        self.c_plugin_inst.status = 'cancelled'
        logger.error(f'[CODE18,{job_id}]: Error while running upload job, remote compute ' 
                     f'returned undefined status for job {job_id}')
        self.c_plugin_inst.error_code = 'CODE18'
        self.save_plugin_instance_final_status()
        # TODO: schedule delete job in case there is data in the remote compute 
        # to be deleted and then that job should schedule delete for this upload job
