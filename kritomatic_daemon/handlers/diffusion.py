from krita import Krita
from PyQt5.QtWidgets import QWidget, QStackedWidget, QLabel, QComboBox, QLineEdit, QPushButton, QCheckBox, QSpinBox, QDoubleSpinBox
from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QMouseEvent
from ..decorators import command
import json
import os


class DiffusionHandler:
    def __init__(self):
        self._docker = None
        self._stacked = None

    def _get_docker(self):
        """Get the AI Image Generation docker"""
        if self._docker is None:
            app = Krita.instance()
            window = app.activeWindow()
            for d in window.dockers():
                if d.objectName() == 'imageDiffusion':
                    self._docker = d
                    break
        return self._docker

    def _get_stacked_widget(self):
        """Get the stacked widget containing workspaces"""
        if self._stacked is None:
            docker = self._get_docker()
            if docker:
                for child in docker.findChildren(QWidget):
                    if isinstance(child, QStackedWidget):
                        self._stacked = child
                        break
        return self._stacked

    def _ensure_custom_workflow(self):
        """Ensure CustomWorkflowWidget is active"""
        stacked = self._get_stacked_widget()
        if not stacked:
            return False

        for i in range(stacked.count()):
            widget = stacked.widget(i)
            if type(widget).__name__ == 'CustomWorkflowWidget':
                if stacked.currentIndex() != i:
                    stacked.setCurrentIndex(i)
                return True
        return False

    def _get_workflow_params_widget(self):
        """Get the WorkflowParamsWidget inside CustomWorkflowWidget"""
        if not self._ensure_custom_workflow():
            return None

        docker = self._get_docker()
        if not docker:
            return None

        # Find CustomWorkflowWidget
        custom_widget = None
        for child in docker.findChildren(QWidget):
            if type(child).__name__ == 'CustomWorkflowWidget':
                custom_widget = child
                break

        if not custom_widget:
            return None

        # Search for WorkflowParamsWidget by type name
        for param_widget in custom_widget.findChildren(QWidget):
            if type(param_widget).__name__ == 'WorkflowParamsWidget':
                return param_widget

        return None

    def _get_current_workflow_name(self):
        """Get the name of the currently selected workflow"""
        try:
            docker = self._get_docker()
            if not docker:
                return None

            # Find workflow selector
            for combo in docker.findChildren(QComboBox):
                for i in range(combo.count()):
                    text = combo.itemText(i)
                    if 'ComfyUI' in text or 'kritaRMBG' in text:
                        return combo.currentText()
            return None
        except:
            return None

    def _toggle_bool_param(self, bool_widget, switch, target_value):
        """Toggle a boolean parameter by simulating a click at the appropriate position"""
        if bool_widget.value == target_value:
            return

        # Determine click position based on target value
        geometry = switch.geometry()
        if target_value:
            click_x = geometry.width() - 5  # Right side to turn on
        else:
            click_x = 5  # Left side to turn off

        click_y = geometry.height() // 2
        click_pos = QPoint(click_x, click_y)

        # Simulate mouse click
        press_event = QMouseEvent(QMouseEvent.MouseButtonPress, click_pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        release_event = QMouseEvent(QMouseEvent.MouseButtonRelease, click_pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)

        switch.mousePressEvent(press_event)
        switch.mouseReleaseEvent(release_event)

    def execute(self, cmd_type, params):
        """Execute diffusion-related commands"""
        if cmd_type == 'list_workflows':
            return self.list_workflows()
        elif cmd_type == 'switch_workflow':
            return self.switch_workflow(params.get('workflow', ''))
        elif cmd_type == 'get_params':
            return self.get_params()
        elif cmd_type == 'set_param':
            param_name = params.get('param', '')
            value = params.get('value', '')
            return self.set_param(param_name, value)
        elif cmd_type == 'generate':
            return self.generate()
        elif cmd_type == 'export_params':
            return self.export_params(params)
        elif cmd_type == 'import_params':
            return self.import_params(params)
        return {'success': False, 'message': f'Unknown diffusion command: {cmd_type}'}

    @command(
        category='diffusion',
        help_text='List available workflows',
        args={}
    )
    def list_workflows(self):
        """List available workflows from the selector"""
        try:
            docker = self._get_docker()
            if not docker:
                return {'success': False, 'message': 'AI Image Generation docker not found'}

            # Find workflow selector
            workflow_selector = None
            for combo in docker.findChildren(QComboBox):
                for i in range(combo.count()):
                    text = combo.itemText(i)
                    if 'ComfyUI' in text or 'kritaRMBG' in text:
                        workflow_selector = combo
                        break
                if workflow_selector:
                    break

            if not workflow_selector:
                return {'success': False, 'message': 'Workflow selector not found'}

            workflows = [workflow_selector.itemText(i) for i in range(workflow_selector.count())]
            current = workflow_selector.currentText()

            return {
                'success': True,
                'message': f'Found {len(workflows)} workflows',
                'data': {
                    'current': current,
                    'workflows': workflows
                }
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='diffusion',
        help_text='Switch to a different workflow',
        args={
            '--workflow': {'type': 'str', 'required': True, 'help': 'Name of the workflow to switch to'}
        }
    )
    def switch_workflow(self, workflow_name):
        """Switch to a different workflow"""
        try:
            docker = self._get_docker()
            if not docker:
                return {'success': False, 'message': 'AI Image Generation docker not found'}

            # Find workflow selector
            workflow_selector = None
            for combo in docker.findChildren(QComboBox):
                for i in range(combo.count()):
                    if combo.itemText(i) == workflow_name:
                        workflow_selector = combo
                        break
                if workflow_selector:
                    break

            if not workflow_selector:
                return {'success': False, 'message': f'Workflow "{workflow_name}" not found'}

            for i in range(workflow_selector.count()):
                if workflow_selector.itemText(i) == workflow_name:
                    workflow_selector.setCurrentIndex(i)
                    workflow_selector.activated.emit(i)
                    return {'success': True, 'message': f'Switched to workflow "{workflow_name}"'}

            return {'success': False, 'message': f'Workflow "{workflow_name}" not found'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='diffusion',
        help_text='Get current workflow parameters',
        args={}
    )
    def get_params(self):
        """Get all current parameters from the workflow"""
        try:
            param_widget = self._get_workflow_params_widget()
            if not param_widget:
                return {'success': False, 'message': 'Workflow parameters widget not found'}

            params = {}
            layout = param_widget.layout()
            if not layout:
                return {'success': False, 'message': 'No layout found'}

            # Iterate through layout items (skip index 0 which is a nested layout)
            for i in range(1, layout.count(), 2):
                label_item = layout.itemAt(i)
                if not label_item or not label_item.widget():
                    continue

                label = label_item.widget()
                if not isinstance(label, QLabel):
                    continue

                label_text = label.text()
                if not label_text or label_text == 'Workflow Parameters':
                    continue

                # Get control at i+1
                if i + 1 >= layout.count():
                    continue

                control_item = layout.itemAt(i + 1)
                if not control_item or not control_item.widget():
                    continue

                control = control_item.widget()
                control_type = type(control).__name__

                # Extract value based on widget type
                if control_type == 'TextParamWidget':
                    params[label_text] = control.text()
                elif control_type == 'FloatParamWidget':
                    params[label_text] = control.value
                elif control_type == 'BoolParamWidget':
                    params[label_text] = control.value
                elif control_type == 'LayerSelect' or control_type == 'QComboBox':
                    params[label_text] = control.currentText()
                elif isinstance(control, QLineEdit):
                    params[label_text] = control.text()
                elif isinstance(control, QSpinBox):
                    params[label_text] = control.value()
                elif isinstance(control, QDoubleSpinBox):
                    params[label_text] = control.value()
                elif isinstance(control, QCheckBox):
                    params[label_text] = control.isChecked()
                else:
                    params[label_text] = f"Unknown type: {control_type}"

            return {
                'success': True,
                'message': f'Found {len(params)} parameters',
                'data': params
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='diffusion',
        help_text='Set a workflow parameter',
        args={
            '--param': {'type': 'str', 'required': True, 'help': 'Parameter name'},
            '--value': {'type': 'str', 'required': True, 'help': 'Parameter value'}
        }
    )
    def set_param(self, param_name, value):
        """Set a workflow parameter by name"""
        try:
            param_widget = self._get_workflow_params_widget()
            if not param_widget:
                return {'success': False, 'message': 'Workflow parameters widget not found'}

            layout = param_widget.layout()
            if not layout:
                return {'success': False, 'message': 'No layout found'}

            # Find the label with matching text
            for i in range(1, layout.count(), 2):
                label_item = layout.itemAt(i)
                if not label_item or not label_item.widget():
                    continue

                label = label_item.widget()
                if not isinstance(label, QLabel):
                    continue

                if label.text() != param_name:
                    continue

                # Found the label, get control at i+1
                if i + 1 >= layout.count():
                    return {'success': False, 'message': f'No control found for "{param_name}"'}

                control_item = layout.itemAt(i + 1)
                if not control_item or not control_item.widget():
                    return {'success': False, 'message': f'No control widget for "{param_name}"'}

                control = control_item.widget()
                control_type = type(control).__name__

                # Handle different widget types
                if control_type == 'TextParamWidget':
                    control.setText(value)
                    return {'success': True, 'message': f'Set "{param_name}" to "{value}"'}

                elif control_type == 'FloatParamWidget':
                    try:
                        float_val = float(value)
                        # Clamp to min/max if available
                        if hasattr(control, 'param'):
                            float_val = max(control.param.min, min(control.param.max, float_val))
                        control.value = float_val
                        return {'success': True, 'message': f'Set "{param_name}" to {float_val}'}
                    except ValueError:
                        return {'success': False, 'message': f'Invalid float value for "{param_name}": {value}'}

                elif control_type == 'BoolParamWidget':
                    target_value = value.lower() in ['true', '1', 'yes', 'on']
                    # Find SwitchWidget inside BoolParamWidget
                    switch = None
                    for child in control.children():
                        if isinstance(child, QWidget) and type(child).__name__ == 'SwitchWidget':
                            switch = child
                            break

                    if switch:
                        self._toggle_bool_param(control, switch, target_value)
                    else:
                        control.value = target_value
                    return {'success': True, 'message': f'Set "{param_name}" to {target_value}'}

                elif control_type == 'LayerSelect' or control_type == 'QComboBox':
                    for idx in range(control.count()):
                        if control.itemText(idx) == value:
                            control.setCurrentIndex(idx)
                            return {'success': True, 'message': f'Set "{param_name}" to "{value}"'}
                    return {'success': False, 'message': f'Value "{value}" not found in {param_name} options'}

                elif isinstance(control, QLineEdit):
                    control.setText(value)
                    return {'success': True, 'message': f'Set "{param_name}" to "{value}"'}

                elif isinstance(control, QSpinBox):
                    try:
                        int_val = int(value)
                        control.setValue(int_val)
                        return {'success': True, 'message': f'Set "{param_name}" to {int_val}'}
                    except ValueError:
                        return {'success': False, 'message': f'Invalid integer value for "{param_name}": {value}'}

                elif isinstance(control, QDoubleSpinBox):
                    try:
                        float_val = float(value)
                        control.setValue(float_val)
                        return {'success': True, 'message': f'Set "{param_name}" to {float_val}'}
                    except ValueError:
                        return {'success': False, 'message': f'Invalid float value for "{param_name}": {value}'}

                elif isinstance(control, QCheckBox):
                    target_value = value.lower() in ['true', '1', 'yes', 'on']
                    control.setChecked(target_value)
                    return {'success': True, 'message': f'Set "{param_name}" to {target_value}'}

                else:
                    return {'success': False, 'message': f'Unsupported control type for "{param_name}": {control_type}'}

            return {'success': False, 'message': f'Parameter "{param_name}" not found'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='diffusion',
        help_text='Export current workflow parameters to JSON',
        args={
            '--output': {'type': 'str', 'required': False, 'help': 'Output file path (optional, prints to stdout if not specified)'}
        }
    )
    def export_params(self, params):
        """Export current workflow parameters to JSON"""
        try:
            # Get current parameters
            result = self.get_params()
            if not result.get('success'):
                return result

            data = {
                'workflow': self._get_current_workflow_name(),
                'parameters': result.get('data', {})
            }

            output_file = params.get('output', None)
            json_str = json.dumps(data, indent=2)

            if output_file:
                with open(output_file, 'w') as f:
                    f.write(json_str)
                return {'success': True, 'message': f'Parameters exported to {output_file}'}
            else:
                # Return the JSON string as data, let client print it
                return {
                    'success': True,
                    'message': 'Parameters exported',
                    'data': {'json': json_str}
                }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='diffusion',
        help_text='Import parameters from JSON file or string',
        args={
            '--source': {'type': 'str', 'required': True, 'help': 'JSON file path or JSON string'},
            '--apply': {'type': 'bool', 'default': True, 'help': 'Apply parameters immediately (default: True)'}
        }
    )
    def import_params(self, params):
        """Import parameters from JSON file or string"""
        try:
            source = params.get('source', '')
            apply_immediately = params.get('apply', True)

            # Parse JSON from file or string
            if os.path.exists(source):
                with open(source, 'r') as f:
                    data = json.load(f)
            else:
                data = json.loads(source)

            # Extract parameters
            if 'parameters' in data:
                parameters = data['parameters']
            else:
                parameters = data

            # Optionally switch workflow first
            if 'workflow' in data and data['workflow']:
                workflow_name = data['workflow']
                print(f"Switching to workflow: {workflow_name}")
                self.switch_workflow(workflow_name)

            # Apply parameters
            if apply_immediately:
                results = []
                for param_name, param_value in parameters.items():
                    value_str = str(param_value)
                    result = self.set_param(param_name, value_str)
                    results.append({
                        'param': param_name,
                        'value': param_value,
                        'success': result.get('success', False),
                        'message': result.get('message', '')
                    })

                success_count = sum(1 for r in results if r['success'])
                return {
                    'success': True,
                    'message': f'Imported {success_count}/{len(results)} parameters',
                    'data': {'results': results}
                }
            else:
                return {
                    'success': True,
                    'message': f'Parsed {len(parameters)} parameters (not applied)',
                    'data': {'parameters': parameters}
                }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='diffusion',
        help_text='Trigger image generation',
        args={}
    )
    def generate(self):
        """Click the generate button"""
        try:
            docker = self._get_docker()
            if not docker:
                return {'success': False, 'message': 'AI Image Generation docker not found'}

            if not self._ensure_custom_workflow():
                return {'success': False, 'message': 'Could not switch to CustomWorkflowWidget'}

            # Find CustomWorkflowWidget
            custom_widget = None
            for child in docker.findChildren(QWidget):
                if type(child).__name__ == 'CustomWorkflowWidget':
                    custom_widget = child
                    break

            if not custom_widget:
                return {'success': False, 'message': 'CustomWorkflowWidget not found'}

            # Find GenerateButton inside CustomWorkflowWidget
            generate_btn = None
            for btn in custom_widget.findChildren(QPushButton):
                if type(btn).__name__ == 'GenerateButton':
                    generate_btn = btn
                    break

            if not generate_btn:
                return {'success': False, 'message': 'Generate button not found'}

            generate_btn.click()
            return {'success': True, 'message': 'Generation triggered'}
        except Exception as e:
            return {'success': False, 'message': str(e)}
