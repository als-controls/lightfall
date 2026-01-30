# Claude Assistant

LUCID includes an integrated Claude AI assistant that understands the application and can help with experiments, answer questions, and even control panels through natural language.

## Getting Started

### Opening the Claude Panel

The Claude panel is available from **View** > **Panels** > **Claude** or may already be in the default layout.

### Configuration

The Claude assistant requires an API key:

1. Go to **Preferences** > **Claude Assistant**
2. Enter your Anthropic API key
3. Optionally configure model selection and other settings

Alternatively, set the `ANTHROPIC_API_KEY` environment variable before launching LUCID.

## Using the Assistant

### Basic Interaction

Type your message in the input area and press Enter to send. Claude responds in the chat area above.

**Examples of what you can ask**:

- "How do I run a grid scan?"
- "What detectors are available?"
- "Show me the current motor positions"
- "Help me understand this error message"

### Natural Language Control

Claude can control LUCID through integrated tools. Instead of navigating menus, you can ask Claude to perform actions:

**Panel Management**:
- "Open the Devices panel"
- "Show me the Bluesky panel"
- "Close the logging panel"

**Information Queries**:
- "What panels are available?"
- "List the open panels"
- "Show me information about the Bluesky panel"

**Device Operations**:
- "What devices are connected?"
- "Show the current position of motor X"

## Capabilities

### What Claude Can Do

Claude has access to tools that let it:

1. **Manage Panels**: Open, close, activate, and query panel states
2. **Inspect Widgets**: Examine UI components and their properties
3. **Query Devices**: Access device information from the catalog
4. **Understand Plans**: Explain available plans and their parameters
5. **Provide Help**: Answer questions about LUCID features and procedures

### What Claude Cannot Do

For safety, some operations are restricted:

- Claude cannot execute plans directly without user confirmation
- Critical device operations require user approval
- System configuration changes must be done through Preferences

## Skills

Claude's capabilities can be extended through skills. Skills are domain-specific knowledge and tools that Claude can use.

### Viewing Available Skills

Go to **Preferences** > **Claude Assistant** to see available skills. Enabled skills appear in Claude's system prompt and provide additional tools.

### Using Skill Capabilities

When a skill is enabled, simply ask Claude about topics in that domain. For example, if a beamline-specific skill is enabled:

- "Help me with the sample alignment procedure"
- "What's the correct energy range for this measurement?"

## Example Conversations

### Getting Help

```
You: How do I run a 2D scan?

Claude: To run a 2D scan (grid scan) in LUCID:

1. Open the Bluesky panel from View > Panels > Bluesky
2. Search for "grid_scan" in the plan selector
3. Select the grid_scan plan
4. Configure the parameters:
   - Detectors: Select your detector(s)
   - Motor 1 and Motor 2: Choose the two motors for X and Y
   - Start/Stop for each motor: Define the scan range
   - Num points: Set the grid resolution
5. Click Run to start the scan

Would you like me to open the Bluesky panel for you?
```

### Controlling Panels

```
You: Open the devices panel and show me what motors are available

Claude: I'll open the Devices panel for you.

[Claude opens the Devices panel]

The Devices panel is now open. Based on the device catalog,
here are the available motors:

- sample_x: Sample X translation
- sample_y: Sample Y translation
- sample_z: Sample Z translation
- theta: Sample rotation
- energy: Monochromator energy

Would you like details about any specific motor?
```

### Understanding Errors

```
You: The scan stopped with an error. What happened?

Claude: Let me check the recent activity. Based on the logbook
and logging panel, the scan stopped because:

Error: Motor 'sample_x' soft limit reached at position 25.5mm

This means the requested scan range exceeded the motor's
configured soft limits. The current soft limits for sample_x
are 0mm to 25mm.

Options to resolve this:
1. Adjust your scan range to stay within 0-25mm
2. Contact your beamline scientist if the limits need adjustment

Would you like me to help you reconfigure the scan?
```

## Tips for Effective Use

### Be Specific

More specific questions get better answers:

- Instead of "Run a scan", try "Run a grid scan on sample_x and sample_y from -5 to 5"
- Instead of "What's wrong?", try "Why did my last scan fail?"

### Ask for Explanations

Claude can explain concepts and procedures:

- "Explain what fly scans are and when to use them"
- "What's the difference between absolute and relative scans?"

### Use for Learning

Claude is useful for learning LUCID's features:

- "What can I do with the Logbook panel?"
- "Show me all the keyboard shortcuts"
- "What types of plans are available?"

## Troubleshooting

### Claude doesn't respond

- Check that the API key is configured correctly in Preferences
- Verify network connectivity to the Anthropic API
- Check the Logging panel for error messages

### Claude can't perform an action

Some actions require appropriate permissions. Ensure you're logged in with sufficient privileges.

### Responses are slow

- Large conversations may slow down responses
- Start a new conversation to clear history
- Check network latency to the API server
