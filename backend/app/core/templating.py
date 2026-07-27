"""Template engine for variable substitution in commands."""
import re
import json
from typing import Any, Dict, List, Optional
from dataclasses import dataclass


@dataclass
class TemplateVariable:
    """Parsed template variable."""
    raw: str
    execution_id: Optional[int] = None
    item_name: Optional[str] = None
    key: str = ""
    index: Optional[int] = None
    is_latest: bool = False


class TemplateEngine:
    """
    Template engine for processing command templates with variable substitution.
    
    Supports:
    - Basic variables: {target}, {port}
    - Execution references: {1.open_ports}, {2.services[0]}
    - Latest execution: {latest:nmap_scan.open_ports}
    - Array indexing: {1.hosts[0]}, {1.ports[*]}
    - Nested access: {1.services[0].name}
    """
    
    # Pattern to match template variables
    VARIABLE_PATTERN = re.compile(
        r'\{(?:'
        r'(?P<latest>latest:)?'           # Optional "latest:" prefix
        r'(?:'
        r'(?P<exec_id>\d+)'                # Execution ID (numeric)
        r'|(?P<item_name>[a-zA-Z0-9_\s-]+)'  # Or item name (allow spaces/dashes)
        r')'
        r'\.(?P<key>[a-zA-Z_][a-zA-Z0-9_\.\[\]\*]*)'  # Key path
        r'|'
        r'(?P<simple_var>[a-zA-Z_][a-zA-Z0-9_]*)'  # Simple variable
        r')\}'
    )
    
    # Pattern for array indexing
    INDEX_PATTERN = re.compile(r'\[(\d+|\*)\]')
    
    def __init__(self):
        """Initialize template engine."""
        pass
    
    def parse_variable(self, match: re.Match) -> TemplateVariable:
        """Parse a template variable match into structured data."""
        groups = match.groupdict()
        
        if groups.get('simple_var'):
            return TemplateVariable(
                raw=match.group(0),
                key=groups['simple_var']
            )
        
        return TemplateVariable(
            raw=match.group(0),
            execution_id=int(groups['exec_id']) if groups.get('exec_id') else None,
            item_name=groups.get('item_name'),
            key=groups.get('key', ''),
            is_latest=bool(groups.get('latest'))
        )
    
    def extract_variables(self, template: str) -> List[TemplateVariable]:
        """Extract all variables from a template string."""
        return [self.parse_variable(m) for m in self.VARIABLE_PATTERN.finditer(template)]
    
    def get_nested_value(self, data: Any, key_path: str) -> Any:
        """
        Get a nested value from data using a key path.
        
        Supports:
        - Dot notation: "services.0.name"
        - Array indexing: "hosts[0]", "ports[*]"
        """
        if not key_path:
            return data
        
        current = data
        
        # Parse key path
        parts = []
        remaining = key_path
        
        while remaining:
            # Check for array index
            index_match = self.INDEX_PATTERN.match(remaining)
            if remaining.startswith('[') and index_match:
                idx = index_match.group(1)
                parts.append(('index', idx))
                remaining = remaining[index_match.end():]
                if remaining.startswith('.'):
                    remaining = remaining[1:]
                continue
            
            # Find next separator
            dot_pos = remaining.find('.')
            bracket_pos = remaining.find('[')
            
            if dot_pos == -1 and bracket_pos == -1:
                parts.append(('key', remaining))
                break
            elif dot_pos == -1:
                parts.append(('key', remaining[:bracket_pos]))
                remaining = remaining[bracket_pos:]
            elif bracket_pos == -1:
                parts.append(('key', remaining[:dot_pos]))
                remaining = remaining[dot_pos + 1:]
            elif dot_pos < bracket_pos:
                parts.append(('key', remaining[:dot_pos]))
                remaining = remaining[dot_pos + 1:]
            else:
                parts.append(('key', remaining[:bracket_pos]))
                remaining = remaining[bracket_pos:]
        
        # Navigate through parts
        for part_type, part_value in parts:
            if current is None:
                return None
            
            if part_type == 'key':
                if isinstance(current, dict):
                    current = current.get(part_value)
                elif hasattr(current, part_value):
                    current = getattr(current, part_value)
                else:
                    return None
            elif part_type == 'index':
                if part_value == '*':
                    # Return all items
                    if isinstance(current, (list, tuple)):
                        return list(current)
                    return None
                else:
                    idx = int(part_value)
                    if isinstance(current, (list, tuple)) and 0 <= idx < len(current):
                        current = current[idx]
                    else:
                        return None
        
        return current
    
    def render(
        self,
        template: str,
        variables: Dict[str, Any],
        execution_outputs: Optional[Dict[int, Dict[str, Any]]] = None,
        item_name_to_execution: Optional[Dict[str, int]] = None,
        latest_item_name_to_execution: Optional[Dict[str, int]] = None,
    ) -> str:
        """
        Render a template with variable substitution.

        Args:
            template: Command template string
            variables: Simple variable values
            execution_outputs: Mapping of execution_id -> parsed_output
            item_name_to_execution: Mapping of item_name -> execution_id (first/any)
            latest_item_name_to_execution: Mapping of item_name -> execution_id (most recent by completed_at)

        Returns:
            Rendered command string
        """
        execution_outputs = execution_outputs or {}
        item_name_to_execution = item_name_to_execution or {}
        latest_item_name_to_execution = latest_item_name_to_execution or item_name_to_execution

        def replace_variable(match: re.Match) -> str:
            var = self.parse_variable(match)

            # Simple variable
            if var.execution_id is None and var.item_name is None:
                value = variables.get(var.key)
                if value is None:
                    return match.group(0)  # Keep original if not found
                return self._format_value(value)

            # Execution reference
            exec_id = var.execution_id

            # Resolve item name to execution ID
            if var.item_name:
                if var.is_latest:
                    exec_id = latest_item_name_to_execution.get(var.item_name)
                else:
                    exec_id = item_name_to_execution.get(var.item_name)
            
            if exec_id is None:
                return match.group(0)  # Keep original if not resolved
            
            # Get output data
            output_data = execution_outputs.get(exec_id, {})
            value = self.get_nested_value(output_data, var.key)
            
            if value is None:
                return match.group(0)  # Keep original if not found
            
            # Auto-unwrap ParsedResult structure
            if isinstance(value, dict) and "value" in value and "data_type" in value:
                value = value["value"]
            
            return self._format_value(value)
        
        return self.VARIABLE_PATTERN.sub(replace_variable, template)
    
    def _format_value(self, value: Any) -> str:
        """Format a value for command line use."""
        if isinstance(value, (list, tuple)):
            # Join list items with space
            return ' '.join(str(v) for v in value)
        elif isinstance(value, dict):
            return json.dumps(value)
        elif isinstance(value, bool):
            return str(value).lower()
        else:
            return str(value)
    
    def validate_template(self, template: str) -> Dict[str, Any]:
        """
        Validate a template and return information about required variables.
        
        Returns:
            {
                "valid": True/False,
                "variables": ["var1", "var2"],
                "execution_refs": [{"id": 1, "key": "output"}],
                "errors": ["error message"]
            }
        """
        result = {
            "valid": True,
            "variables": [],
            "execution_refs": [],
            "errors": []
        }
        
        variables = self.extract_variables(template)
        
        for var in variables:
            if var.execution_id is None and var.item_name is None:
                # Simple variable
                if var.key not in result["variables"]:
                    result["variables"].append(var.key)
            else:
                # Execution reference
                result["execution_refs"].append({
                    "execution_id": var.execution_id,
                    "item_name": var.item_name,
                    "key": var.key,
                    "is_latest": var.is_latest
                })
        
        return result


# Singleton instance
template_engine = TemplateEngine()
