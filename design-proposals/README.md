# Cozystack Design Proposals

This folder contains design proposals for features and architectural decisions that impact Cozystack as a whole.

## Purpose

Design proposals allow community members to share their ideas early, get feedback, and build consensus before implementation begins.
The process is especially useful for:

- Architectural decisions that will shape multiple features in the future
- Features that require choosing between competing architectural approaches with tradeoffs
- Decisions that affect multiple components or APIs

By proposing designs up front, we aim to reduce risk and ensure the project evolves in a coordinated, community-driven way.

**Note:** Using the design proposal process is optional, but strongly encouraged—especially for non-trivial or cross-cutting work.

## How to Create a Proposal

1.  Make a new directory under `./design-proposals` and create a README.md file in it:

    ```
    ./design-proposals/<proposal-title>/README.md
    ```
    
    Describe your proposal in this file.
    
    Put all additional materials, such as diagrams, reference docs, and others, in the same folder.
    Note that for diagrams you can use [Mermaid.js](https://mermaid.js.org/) markup, which is natively rendered on GitHub
    directly in the Markdown files.
    
1.  Remember to include basic metadata at the top:

    - Title
    - Author(s)
    - Date
    
1.  As with all commits in CNCF projects, please sign the commit for a DCO check:
    
    ```bash
    git commit --signoff 
    ```
    
1.  Submit a pull request with your proposal and request feedback.

To bring attention to your proposal, share it in the Cozystack community:

-   [Community chat in Telegram](https://t.me/cozystack)
-   [CNCF Slack – #cozystack](https://cloud-native.slack.com/archives/C08BQJD95J7)
-   [Kubernetes Slack – #cozystack](https://kubernetes.slack.com/archives/C06L3CPRVN1)
-   Bi-weekly Cozystack community calls

## Approval Process

Proposals are reviewed in GitHub PRs. Once a proposal receives consensus from core maintainers (via `/lgtm` and comments), it will be merged and considered accepted. Merged proposals serve as a reference, not a binding spec.

## Proposal Drift

We understand that real-world implementation may diverge from initial designs. This is normal and expected.

While it's helpful to update the proposal if the divergence is significant, the **codebase and user documentation are the final source of truth**.

## Inspiration

This process is inspired by [KubeVirt's design proposals](https://github.com/kubevirt/community/tree/main/design-proposals) and [Kubernetes Enhancement Proposals](https://github.com/kubernetes/enhancements).