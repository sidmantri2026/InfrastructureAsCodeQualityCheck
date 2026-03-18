# Automation Code Review Rules
# Technology: Ansible
# Category: [e.g. Naming Conventions / Security / Idempotency / Style]
# Author: [Your Name]
# Last Updated: [Date]

---

## How to Use This Template

Fill in each section below for every rule your team wants enforced.
You do NOT need to write code or regex — just write in plain English.
The more specific you are, the more accurately the rule can be automated.

Delete this "How to Use" section before sharing the file.

---

## Rule 1

**Rule Name:**
Write a short title for this rule. Example: "Task names must always be present"

**Severity:**
Pick one: CRITICAL / ERROR / WARNING / INFO

- CRITICAL — Security risk or will definitely break things. Must fix before merge.
- ERROR    — Clear standards violation. Must fix.
- WARNING  — Best practice not followed. Should fix.
- INFO     — Style or documentation suggestion. Nice to fix.

**What is the rule?**
Describe what the rule requires or forbids, in plain English.
Example: "Every Ansible task must have a name field. Tasks without a name are not allowed."

**Why does this rule exist?**
Explain the reason behind the rule. This appears in the report to educate developers.
Example: "Without a name, Ansible shows the module name in logs (e.g. TASK [copy]) which makes it impossible to understand what is happening during a run, especially when debugging failures."

**What does BAD code look like?**
Paste an example of code that VIOLATES this rule.

```yaml
# Example of bad code here
- copy:
    src: nginx.conf
    dest: /etc/nginx/nginx.conf
```

**What does GOOD code look like?**
Paste an example of code that FOLLOWS this rule.

```yaml
# Example of good code here
- name: Copy nginx configuration to server
  copy:
    src: nginx.conf
    dest: /etc/nginx/nginx.conf
```

**Is this rule always enforced, or are there exceptions?**
Describe any situations where this rule should NOT apply.
Example: "This rule does not apply to handler blocks."
If there are no exceptions, write: None.

**How strict should matching be?**
Should the tool flag every single occurrence, or only obvious cases?
Example: "Flag every task that is missing a name field, no exceptions."

---

## Rule 2

**Rule Name:**


**Severity:**


**What is the rule?**


**Why does this rule exist?**


**What does BAD code look like?**

```yaml

```

**What does GOOD code look like?**

```yaml

```

**Is this rule always enforced, or are there exceptions?**


**How strict should matching be?**


---

## Rule 3

**Rule Name:**


**Severity:**


**What is the rule?**


**Why does this rule exist?**


**What does BAD code look like?**

```yaml

```

**What does GOOD code look like?**

```yaml

```

**Is this rule always enforced, or are there exceptions?**


**How strict should matching be?**


---

## Rule 4

**Rule Name:**


**Severity:**


**What is the rule?**


**Why does this rule exist?**


**What does BAD code look like?**

```yaml

```

**What does GOOD code look like?**

```yaml

```

**Is this rule always enforced, or are there exceptions?**


**How strict should matching be?**


---

## Rule 5

**Rule Name:**


**Severity:**


**What is the rule?**


**Why does this rule exist?**


**What does BAD code look like?**

```yaml

```

**What does GOOD code look like?**

```yaml

```

**Is this rule always enforced, or are there exceptions?**


**How strict should matching be?**


---

## Notes for the Architect / Rule Author

Use this section for any additional context, references, or decisions
that do not fit into a specific rule above.

Example:
- "Rules 2 and 3 are related — if Rule 2 passes, Rule 3 usually passes too."
- "We adopted these standards based on the Red Hat Ansible Best Practices Guide, 2023."
- "Rule 4 is aspirational — we want to enforce it but our legacy code will need cleanup first."


---

## Copy this block to add more rules

---

## Rule N

**Rule Name:**


**Severity:**


**What is the rule?**


**Why does this rule exist?**


**What does BAD code look like?**

```yaml

```

**What does GOOD code look like?**

```yaml

```

**Is this rule always enforced, or are there exceptions?**


**How strict should matching be?**
