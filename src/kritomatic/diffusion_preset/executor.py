"""Executor for diffusion preset operations (save, load, list, delete, info, export, import)"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional

from .library import DiffusionPresetLibrary


class DiffusionPresetExecutor:
    """Execute diffusion preset operations using the daemon"""

    def __init__(self, client):
        self.client = client
        self.library = DiffusionPresetLibrary()

    def _get_current_params(self) -> Optional[Dict]:
        """Get current parameters from daemon"""
        response = self.client.execute('get_params')
        if response and response.get('status') == 'success':
            return response.get('data', {})
        return None

    def _get_current_workflow(self) -> Optional[str]:
        """Get current workflow from daemon"""
        response = self.client.execute('list_workflows')
        if response and response.get('status') == 'success':
            return response.get('data', {}).get('current')
        return None

    def _apply_params(self, params: Dict) -> List[Dict]:
        """Apply parameters to daemon"""
        results = []
        for param_name, param_value in params.items():
            value_str = str(param_value)
            response = self.client.execute('set_param', param=param_name, value=value_str)
            results.append({
                'param': param_name,
                'value': param_value,
                'success': response.get('status') == 'success' if response else False,
                'message': response.get('message', '') if response else 'No response'
            })
        return results

    def save(self, name: str, workflow_override: Optional[str] = None) -> Dict[str, Any]:
        """Save current settings as a preset"""
        try:
            if not name:
                return {'success': False, 'message': 'Preset name required'}

            if self.library.exists(name):
                return {'success': False, 'message': f'Preset "{name}" already exists'}

            # Get current workflow and parameters
            if workflow_override:
                # Temporarily switch to override workflow
                original_workflow = self._get_current_workflow()
                self.client.execute('switch_workflow', workflow=workflow_override)
                workflow_name = workflow_override
                params = self._get_current_params()
                # Switch back
                if original_workflow:
                    self.client.execute('switch_workflow', workflow=original_workflow)
            else:
                workflow_name = self._get_current_workflow()
                params = self._get_current_params()

            if not params:
                return {'success': False, 'message': 'Failed to get current parameters'}

            preset_data = {
                'name': name,
                'workflow': workflow_name,
                'parameters': params,
                'created': datetime.now().isoformat(),
                'modified': datetime.now().isoformat()
            }

            if self.library.save(name, preset_data):
                info = self.library.get_info(name)
                return {
                    'success': True,
                    'message': f'Preset "{name}" saved',
                    'data': info
                }
            else:
                return {'success': False, 'message': f'Failed to save preset "{name}"'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def load(self, name: str) -> Dict[str, Any]:
        """Load a preset and apply parameters"""
        try:
            if not name:
                return {'success': False, 'message': 'Preset name required'}

            preset_data = self.library.load(name)
            if not preset_data:
                return {'success': False, 'message': f'Preset "{name}" not found'}

            workflow_name = preset_data.get('workflow')
            parameters = preset_data.get('parameters', {})

            # Switch to the saved workflow if needed
            if workflow_name:
                current_workflow = self._get_current_workflow()
                if current_workflow != workflow_name:
                    self.client.execute('switch_workflow', workflow=workflow_name)

            # Apply parameters
            results = self._apply_params(parameters)
            success_count = sum(1 for r in results if r['success'])

            return {
                'success': True,
                'message': f'Loaded preset "{name}": {success_count}/{len(results)} parameters applied',
                'data': {'results': results}
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def list_presets(self) -> Dict[str, Any]:
        """List all saved presets"""
        try:
            presets = []
            for name in self.library.list_presets():
                info = self.library.get_info(name)
                if info:
                    presets.append(info)

            return {
                'success': True,
                'message': f'Found {len(presets)} presets',
                'data': {'presets': presets}
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def delete(self, name: str) -> Dict[str, Any]:
        """Delete a preset"""
        try:
            if not name:
                return {'success': False, 'message': 'Preset name required'}

            if self.library.delete(name):
                return {'success': True, 'message': f'Preset "{name}" deleted'}
            else:
                return {'success': False, 'message': f'Preset "{name}" not found'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def info(self, name: str) -> Dict[str, Any]:
        """Show detailed information about a preset"""
        try:
            if not name:
                return {'success': False, 'message': 'Preset name required'}

            info = self.library.get_info(name)
            if info:
                return {
                    'success': True,
                    'message': f'Preset "{name}" info',
                    'data': info
                }
            else:
                return {'success': False, 'message': f'Preset "{name}" not found'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def export_preset(self, name: str, output_path: Optional[str] = None) -> Dict[str, Any]:
        """Export a preset to a file"""
        try:
            if not name:
                return {'success': False, 'message': 'Preset name required'}

            preset_data = self.library.load(name)
            if not preset_data:
                return {'success': False, 'message': f'Preset "{name}" not found'}

            json_str = json.dumps(preset_data, indent=2)

            if output_path:
                with open(output_path, 'w') as f:
                    f.write(json_str)
                return {'success': True, 'message': f'Preset "{name}" exported to {output_path}'}
            else:
                # Print to stdout for piping
                print(json_str)
                return {'success': True, 'message': 'Preset exported to stdout'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def import_preset(self, source: str, save_name: Optional[str] = None) -> Dict[str, Any]:
        """Import a preset from a file"""
        try:
            if not source:
                return {'success': False, 'message': 'Source file required'}

            source_path = Path(source)
            if not source_path.exists():
                return {'success': False, 'message': f'Source file not found: {source}'}

            with open(source_path, 'r') as f:
                preset_data = json.load(f)

            # Determine preset name
            if save_name:
                preset_name = save_name
            elif 'name' in preset_data:
                preset_name = preset_data['name']
            else:
                preset_name = source_path.stem

            # Check if already exists
            if self.library.exists(preset_name):
                return {'success': False, 'message': f'Preset "{preset_name}" already exists'}

            # Update metadata
            preset_data['name'] = preset_name
            preset_data['modified'] = datetime.now().isoformat()
            if 'created' not in preset_data:
                preset_data['created'] = datetime.now().isoformat()

            if self.library.save(preset_name, preset_data):
                info = self.library.get_info(preset_name)
                return {
                    'success': True,
                    'message': f'Preset "{preset_name}" imported',
                    'data': info
                }
            else:
                return {'success': False, 'message': f'Failed to import preset'}
        except Exception as e:
            return {'success': False, 'message': str(e)}
