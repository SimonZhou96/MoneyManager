# KERDAR - Modern React Workflow Designer

[![npm version](https://img.shields.io/npm/v/@kerdar/core.svg)](https://www.npmjs.com/package/@kerdar/core)
[![npm version](https://img.shields.io/npm/v/@kerdar/nodes-standard.svg)](https://www.npmjs.com/package/@kerdar/nodes-standard)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A production-ready, embeddable React workflow designer library inspired by n8n. Build powerful workflow automation into your application with a beautiful, modern UI.

**[GitHub](https://github.com/navid-kianfar/kerdar)** | **[npm: @kerdar/core](https://www.npmjs.com/package/@kerdar/core)** | **[npm: @kerdar/nodes-standard](https://www.npmjs.com/package/@kerdar/nodes-standard)**

## Features

- **Visual Workflow Designer** - Drag & drop nodes, connect them visually
- **34+ Built-in Nodes** - Triggers, actions, logic, data transformations, integrations
- **Expression Editor** - Monaco-based editor with autocomplete for data references
- **Client-side Execution** - Run workflows directly in the browser
- **TypeScript First** - Full type safety with comprehensive type definitions
- **Themeable** - Light/dark mode with customizable colors
- **Extensible** - Add custom nodes, categories, and credential types

## Installation

```bash
# Using pnpm (recommended)
pnpm add @kerdar/core @kerdar/nodes-standard

# Using npm
npm install @kerdar/core @kerdar/nodes-standard

# Using yarn
yarn add @kerdar/core @kerdar/nodes-standard
```

## Quick Start

```tsx
import { useState } from 'react';
import {
  WorkflowDesigner,
  registerNodes,
  useWorkflowStore,
  type Workflow,
} from '@kerdar/core';
import { standardNodes } from '@kerdar/nodes-standard';
import '@kerdar/core/dist/style.css';

// Register standard nodes
registerNodes(standardNodes);

function App() {
  const [workflow, setWorkflow] = useState<Workflow>({
    id: 'workflow-1',
    name: 'My Workflow',
    nodes: [],
    edges: [],
  });

  return (
    <div style={{ height: '100vh' }}>
      <WorkflowDesigner
        workflow={workflow}
        onChange={setWorkflow}
      />
    </div>
  );
}
```

## Creating Custom Nodes

### Basic Custom Node

```tsx
import { registerNode, NodeCategory, PropertyType, type NodeTypeDefinition } from '@kerdar/core';

const MyCustomNode: NodeTypeDefinition = {
  type: 'my-custom-node',
  version: 1,
  name: 'myCustomNode',
  displayName: 'My Custom Node',
  description: 'A custom node that does something',
  icon: 'Zap', // Lucide icon name
  iconColor: '#FF6B6B',
  category: NodeCategory.Action, // or 'action' string
  group: ['custom', 'transform'],

  // Define inputs
  inputs: [
    { type: 'main', displayName: 'Input' }
  ],

  // Define outputs
  outputs: [
    { type: 'main', displayName: 'Output' }
  ],

  // Define configurable parameters
  properties: [
    {
      name: 'operation',
      displayName: 'Operation',
      type: PropertyType.Options,
      options: [
        { name: 'Add', value: 'add' },
        { name: 'Multiply', value: 'multiply' },
      ],
      default: 'add',
      required: true,
    },
    {
      name: 'value',
      displayName: 'Value',
      type: PropertyType.Number,
      default: 0,
      description: 'The value to use in the operation',
    },
    {
      name: 'advanced',
      displayName: 'Advanced Options',
      type: PropertyType.Collection,
      default: {},
      values: [
        {
          name: 'precision',
          displayName: 'Decimal Precision',
          type: PropertyType.Number,
          default: 2,
        },
      ],
      // Only show when operation is 'multiply'
      displayOptions: {
        show: {
          operation: ['multiply'],
        },
      },
    },
  ],

  // Execution function
  async execute(context) {
    const inputData = context.getInputData();
    const operation = context.getNodeParameter<string>('operation');
    const value = context.getNodeParameter<number>('value');

    const results = inputData.map(item => ({
      json: {
        ...item.json,
        result: operation === 'add'
          ? (item.json.value as number) + value
          : (item.json.value as number) * value,
      },
    }));

    return { outputData: [results] };
  },
};

// Register the node
registerNode(MyCustomNode);
```

### Custom Category

Categories are automatically created when you register nodes. Just use a new category value:

```tsx
import { NodeCategory } from '@kerdar/core';

// Use built-in categories
const node1 = {
  // ...
  category: NodeCategory.Action, // 'action'
};

// Or create a custom category by using a string
const node2 = {
  // ...
  category: 'my-custom-category' as any, // Will create a new category
};
```

To customize how categories appear in the sidebar, you can filter or group nodes:

```tsx
import { useNodeTypesByCategory, useNodeCategories } from '@kerdar/core';

function CustomSidebar() {
  const categories = useNodeCategories();

  // Get nodes for a specific category
  const actionNodes = useNodeTypesByCategory('action');

  // Custom ordering
  const orderedCategories = ['trigger', 'action', 'logic', 'data', 'my-custom-category'];

  return (
    <div>
      {orderedCategories.map(category => (
        <CategorySection key={category} category={category} />
      ))}
    </div>
  );
}
```

## Working with Workflows

### Workflow Data Structure

```typescript
interface Workflow {
  id: string;
  name: string;
  description?: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  settings?: WorkflowSettings;
  metadata?: {
    createdAt: string;
    updatedAt: string;
    author?: string;
    tags?: string[];
  };
}

interface WorkflowNode {
  id: string;
  type: string;           // References NodeTypeDefinition.type
  name: string;           // Display name
  position: { x: number; y: number };
  parameters: Record<string, any>;
  disabled?: boolean;
  credentials?: Record<string, { id: string; name: string }>;
}

interface WorkflowEdge {
  id: string;
  source: string;         // Source node ID
  target: string;         // Target node ID
  sourceHandle?: string;  // Output handle (e.g., "output-0")
  targetHandle?: string;  // Input handle (e.g., "input-0")
}
```

### Fetching Workflows

```tsx
import { useState, useEffect } from 'react';
import { WorkflowDesigner, type Workflow } from '@kerdar/core';

function WorkflowEditor({ workflowId }: { workflowId: string }) {
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [loading, setLoading] = useState(true);

  // Fetch workflow from your API
  useEffect(() => {
    async function fetchWorkflow() {
      try {
        const response = await fetch(`/api/workflows/${workflowId}`);
        const data = await response.json();
        setWorkflow(data);
      } catch (error) {
        console.error('Failed to fetch workflow:', error);
      } finally {
        setLoading(false);
      }
    }
    fetchWorkflow();
  }, [workflowId]);

  if (loading) return <div>Loading...</div>;
  if (!workflow) return <div>Workflow not found</div>;

  return (
    <WorkflowDesigner
      workflow={workflow}
      onChange={setWorkflow}
    />
  );
}
```

### Saving Workflows

```tsx
import { useWorkflowStore, useIsDirty } from '@kerdar/core';

function SaveButton() {
  const workflow = useWorkflowStore(state => state.workflow);
  const isDirty = useIsDirty();
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      await fetch(`/api/workflows/${workflow.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(workflow),
      });

      // Mark as saved (clears dirty state)
      useWorkflowStore.getState().markAsSaved();
    } catch (error) {
      console.error('Failed to save:', error);
    } finally {
      setSaving(false);
    }
  };

  return (
    <button onClick={handleSave} disabled={!isDirty || saving}>
      {saving ? 'Saving...' : isDirty ? 'Save Changes' : 'Saved'}
    </button>
  );
}
```

### Export/Import Workflows

```tsx
function ExportImportButtons() {
  const workflow = useWorkflowStore(state => state.workflow);
  const setWorkflow = useWorkflowStore(state => state.setWorkflow);

  // Export to JSON file
  const handleExport = () => {
    const json = JSON.stringify(workflow, null, 2);
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);

    const a = document.createElement('a');
    a.href = url;
    a.download = `${workflow.name || 'workflow'}.json`;
    a.click();

    URL.revokeObjectURL(url);
  };

  // Import from JSON file
  const handleImport = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const imported = JSON.parse(e.target?.result as string);
        setWorkflow(imported);
      } catch (error) {
        console.error('Invalid workflow file:', error);
      }
    };
    reader.readAsText(file);
  };

  return (
    <>
      <button onClick={handleExport}>Export</button>
      <input type="file" accept=".json" onChange={handleImport} />
    </>
  );
}
```

## Executing Workflows

### Client-side Execution

```tsx
import { executeWorkflow, useExecutionStore } from '@kerdar/core';

function ExecuteButton() {
  const workflow = useWorkflowStore(state => state.workflow);
  const isExecuting = useExecutionStore(state => state.isExecuting);

  const handleExecute = async () => {
    try {
      const result = await executeWorkflow(workflow, {
        mode: 'manual',
        onProgress: (nodeId, status) => {
          console.log(`Node ${nodeId}: ${status}`);
        },
      });
      console.log('Execution result:', result);
    } catch (error) {
      console.error('Execution failed:', error);
    }
  };

  return (
    <button onClick={handleExecute} disabled={isExecuting}>
      {isExecuting ? 'Running...' : 'Execute'}
    </button>
  );
}
```

### Accessing Execution Data

```tsx
import { useExecutionStore, useNodeStatus, useAllNodeStatus } from '@kerdar/core';

function ExecutionStatus() {
  const isExecuting = useExecutionStore(state => state.isExecuting);
  const nodeStatuses = useAllNodeStatus();
  const executionLog = useExecutionStore(state => state.executionLog);

  // Get output data for a specific node
  const nodeOutputData = useExecutionStore(state => state.nodeOutputData);
  const httpRequestOutput = nodeOutputData['http-request-node-id'];

  return (
    <div>
      <p>Status: {isExecuting ? 'Running' : 'Idle'}</p>

      <h3>Node Statuses:</h3>
      {Object.entries(nodeStatuses).map(([nodeId, status]) => (
        <div key={nodeId}>{nodeId}: {status}</div>
      ))}

      <h3>Execution Log:</h3>
      {executionLog.map((entry, i) => (
        <div key={i}>{entry.message}</div>
      ))}
    </div>
  );
}
```

## Theming

### Light/Dark Mode

```tsx
import { WorkflowDesigner, useThemeActions, ThemeMode } from '@kerdar/core';

function App() {
  const { setMode } = useThemeActions();

  return (
    <>
      <button onClick={() => setMode(ThemeMode.Light)}>Light</button>
      <button onClick={() => setMode(ThemeMode.Dark)}>Dark</button>
      <button onClick={() => setMode(ThemeMode.System)}>System</button>

      <WorkflowDesigner workflow={workflow} onChange={setWorkflow} />
    </>
  );
}
```

### Custom Theme

```tsx
import { WorkflowDesigner, type ThemeConfig } from '@kerdar/core';

const customTheme: Partial<ThemeConfig> = {
  mode: 'dark',
  primaryColor: '#3B82F6',
  accentColor: '#8B5CF6',
  nodeColors: {
    trigger: '#8B5CF6',
    action: '#3B82F6',
    logic: '#F59E0B',
    data: '#10B981',
  },
};

function App() {
  return (
    <WorkflowDesigner
      workflow={workflow}
      onChange={setWorkflow}
      theme={customTheme}
    />
  );
}
```

## API Reference

### Main Components

| Component | Description |
|-----------|-------------|
| `WorkflowDesigner` | Main workflow editor component |
| `NodeSidebar` | Node palette sidebar |
| `NodeDetailsView` | Node configuration panel (NDV) |
| `ExecutionHistory` | Execution history panel |

### Hooks

| Hook | Description |
|------|-------------|
| `useWorkflowStore` | Access workflow state |
| `useWorkflow` | Get current workflow |
| `useNodes` | Get workflow nodes |
| `useEdges` | Get workflow edges |
| `useSelectedNode` | Get selected node |
| `useIsDirty` | Check if workflow has unsaved changes |
| `useExecutionStore` | Access execution state |
| `useIsExecuting` | Check if workflow is executing |
| `useNodeStatus` | Get status for a specific node |
| `useNodeRegistryStore` | Access node registry |
| `useNodeTypes` | Get all registered node types |
| `useNodeCategories` | Get all categories |
| `useThemeStore` | Access theme state |
| `useThemeMode` | Get current theme mode |

### Functions

| Function | Description |
|----------|-------------|
| `registerNode(node)` | Register a single node type |
| `registerNodes(nodes)` | Register multiple node types |
| `getNodeType(type)` | Get node type definition |
| `executeWorkflow(workflow, options)` | Execute a workflow |

## Standard Nodes

### Trigger Nodes
- **Manual Trigger** - Start workflow manually
- **Schedule Trigger** - Cron-based scheduling
- **Webhook Trigger** - HTTP endpoint trigger

### Action Nodes
- **HTTP Request** - Make HTTP requests
- **Code** - Execute JavaScript code
- **Execute Command** - Run shell commands
- **Send Email** - Send emails via SMTP
- **Slack** - Slack API integration

### Logic Nodes
- **If** - Conditional branching
- **Switch** - Multi-way routing
- **Merge** - Combine data from multiple inputs
- **Loop** - Iterate over items
- **Split In Batches** - Process items in batches

### Data Nodes
- **Set Variable** - Set/modify variables
- **Filter** - Filter items by conditions
- **Sort** - Sort items
- **Limit** - Limit number of items
- **Transform** - Map/transform data

### Integration Nodes
- **Redis** - Redis operations
- **RabbitMQ** - Message queue operations
- **MinIO** - Object storage operations

## TypeScript Support

All types are exported from `@kerdar/core`:

```tsx
import type {
  Workflow,
  WorkflowNode,
  WorkflowEdge,
  NodeTypeDefinition,
  NodeProperty,
  NodeExecutionContext,
  NodeExecutionResult,
  ThemeConfig,
} from '@kerdar/core';
```

## License

MIT
