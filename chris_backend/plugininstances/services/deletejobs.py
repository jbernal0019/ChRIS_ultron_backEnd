"""
Delete job manager module that provides the interface for submitting and
checking the execution status of data delete jobs running in a remote compute 
environment (ChRIS / pfcon interface) as well as deleting them when finished.
"""

import logging
import io
import json

from pfconclient.client import JobType
from pfconclient.exceptions import PfconRequestException

from django.utils import timezone

from core.utils import json_zip2str
from plugininstances.models import PluginInstance
from .abstractjobs import PluginInstanceJob


logger = logging.getLogger(__name__)


class PluginInstanceDeleteJob(PluginInstanceJob):
    """
    ``PluginInstanceDeleteJob`` provides a concrete implementation for managing a remote
    data delete job related to a plugin instance.
    """

    def run(self):
        """
        Run the plugin instance data delete job via a call to a remote pfcon service.
        """
        super().run()

        job_id = self.str_job_id        
        pfcon_url = self.pfcon_client.url

        logger.info(f'Submitting data delete job {job_id} to pfcon url '
                    f'-->{pfcon_url}<--')
        try:
            d_resp = self._submit(job_id, {})
        except PfconRequestException as e:
            logger.error(f'[CODE01,{job_id}]: Error submitting data delete job to pfcon '
                         f'url -->{pfcon_url}<--, detail: {str(e)}')
            
            self.c_plugin_inst.error_code = 'CODE01'
            self.c_plugin_inst.status = 'cancelled'  # giving up
            self.save_plugin_instance_final_status()
        else:
            logger.info(f'Successfully submitted data delete job {job_id} to pfcon url '
                        f'-->{pfcon_url}<--, response: {json.dumps(d_resp, indent=4)}')
            
            # update the job status and summary
            self.c_plugin_inst.summary = self.get_job_status_summary(d_resp)
            self.c_plugin_inst.raw = json_zip2str(d_resp)

            # https://github.com/FNNDSC/ChRIS_ultron_backEnd/issues/408
            now = timezone.now()
            self.c_plugin_inst.start_date = now
            self.c_plugin_inst.end_date = now
            self.c_plugin_inst.save()

    def _submit(self, job_id: str, job_descriptors: dict, dfile: io.BytesIO | None = None,
                 timeout: int = 30) -> dict:
        """
        Submit plugin instance data delete job to a remote pfcon service.
        """
        return super()._submit(JobType.DELETE, job_id, job_descriptors, dfile, timeout)

    def check_exec_status(self):
        """
        Check a plugin instance data delete job's execution status. If the associated   
        job's execution time exceeds the maximum set for the remote compute environment  
        then the job is cancelled. Otherwise the job's execution status is fetched from  
        the remote.
        """
        if self.c_plugin_inst.status in ['finishedSuccessfully', 'finishedWithError', 
                                         'cancelled']:
            job_id = self.str_job_id

            if self._job_has_timeout():
                logger.error(f'[CODE13,{job_id}]: Error, delete job exceeded maximum '
                             f'execution time')
                self.c_plugin_inst.error_code = 'CODE13'
                self.cancel_exec()
                return self.c_plugin_inst.status

            pfcon_url = self.pfcon_client.url
            logger.info(f'Sending job status request to pfcon url -->{pfcon_url}<-- for '
                        f'delete job {job_id}')
            try:
                d_resp = self._get_status(job_id)
            except PfconRequestException as e:
                logger.error(f'[CODE02,{job_id}]: Error getting delete job status at '
                             f'pfcon url -->{pfcon_url}<--, detail: {str(e)}')
                return self.c_plugin_inst.status  # return, CUBE will retry later

            logger.info(f'Successful job status response from pfcon url -->{pfcon_url}<--'
                        f' for delete job {job_id}: {json.dumps(d_resp, indent=4)}')
            
            status = d_resp['compute']['status']
            logger.info(f'Current delete job {job_id} remote status = {status}')
            logger.info(f'Current delete job {job_id} plugin instance DB status = '
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
                self._handle_finished_successfully_status()
            elif status == 'finishedWithError':
                self._handle_finished_with_error_status()
            elif status == 'undefined':
                self._handle_undefined_status()
        return self.c_plugin_inst.status

    def _get_status(self, job_id: str, timeout: int = 30) -> dict:
        """
        Get plugin instance data delete job status from a remote pfcon service.
        """
        return super()._get_status(JobType.DELETE, job_id, timeout)

    def cancel_exec(self):
        """
        Cancel a plugin instance data delete job execution. It connects to the remote 
        service to cancel job.
        """
        self.c_plugin_inst.status = 'cancelled'
        self.save_plugin_instance_final_status()
        # TODO: define appropriate actions here

    def delete(self):
        """
        Delete a plugin instance data delete job from the remote compute. It connects to 
        the remote service to delete the job.
        """
        pfcon_url = self.pfcon_client.url
        job_id = self.str_job_id
        logger.info(f'Deleting data delete job {job_id} from pfcon at url '
                    f'-->{pfcon_url}<--')
        try:
            self._delete(job_id)
        except PfconRequestException as e:
            logger.error(f'[CODE12,{job_id}]: Error deleting data delete job from '
                             f'pfcon at url -->{pfcon_url}<--, detail: {str(e)}')
            self.c_plugin_inst.error_code = 'CODE12'
        else:
            logger.info(f'Successfully deleted data delete job {job_id} from pfcon at '
                        f'url -->{pfcon_url}<--')
            if self.c_plugin_inst.error_code == 'CODE12':
                self.c_plugin_inst.error_code = ''

    def _delete(self, job_id: str, timeout: int = 30):
        """
        Delete a plugin instance data delete job from a remote pfcon service.
        """
        super()._delete(JobType.DELETE, job_id, timeout)
        
    def _handle_finished_successfully_status(self):
        """
        Internal method to handle the 'finishedSuccessfully' status returned by the
        remote compute.
        """
        job_id = self.str_job_id
        logger.info(f'Successfully finished plugin instance delete job {job_id}')
        # TODO: schedule delete call for tht delete job and the copy, plugin and upload jobs

    def _handle_finished_with_error_status(self):
        """
        Internal method to handle the 'finishedWithError' status returned by the
        remote compute.
        """
        job_id = self.str_job_id
        # TODO: retry delete job

    def _handle_undefined_status(self):
        """
        Internal method to handle the 'undefined' status returned by the
        remote compute.
        """
        job_id = self.str_job_id
        # TODO: retry delete job
