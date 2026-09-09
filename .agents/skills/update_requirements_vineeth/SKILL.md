---
name: update_requirements-vineeth
description: Reads user_feedback.md, intelligently refactors job_requirements.md with generalized rules based on the feedback, and clears user_feedback.md.
---

# Update Requirements Skill

This skill processes the raw UI feedback gathered by the orchestrator and distills it into intelligent, generalized rules inside the main `job_requirements.md` file.

## Workflow

1.  **Read Files:**
    -   Read `user_feedback.md` (located in the workspace root).
    -   If it's empty or only contains comments, stop and output a success message (nothing to do).
    -   Read `job_requirements.md` (located in the workspace root).

2.  **Analyze and Generalize Feedback:**
    -   Read the raw UI feedback strings.
    -   Deduce generalized rules. For example, if a user rejects a job because "Needs degree in chemistry", you shouldn't just append a constraint about chemistry. You should intelligently add it to the existing `Specialized / Vocational Degrees` or `Advanced Technical Degrees` list in the `## Hard Rejections` section of `job_requirements.md`.
    -   Ignore unhelpful, contextless feedback like "No longer accepting applications" since the requirements file already handles deadlines.

3.  **Update Requirements:**
    -   Use `replace_file_content` to carefully surgically modify `job_requirements.md` with your new generalized rules. Maintain the existing formatting and structure of the document. Do not just append raw strings at the bottom.

4.  **Clear Feedback:**
    -   Once `job_requirements.md` is successfully updated, clear the contents of `user_feedback.md` so that the feedback is not processed twice.
    -   You can overwrite `user_feedback.md` with a clean, empty placeholder:
        ```markdown
        # User Feedback

        Feedback from the UI will be appended here.
        ```

5.  **Summarize:**
    -   Output a brief summary of the rules you deduced and added to the requirements file.
