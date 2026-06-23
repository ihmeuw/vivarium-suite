#!/bin/bash

# Model Lineage Analysis Tool
# A tool for analyzing lineage relationships between model iteration tags
#
# Usage:
#   model [command] [args]
#
# Commands:
#   list                    List all model tags
#   base <tag>              Show what tag/commit a tag was based on
#   contains <tag>          List all tags that contain (descend from) a tag
#   ancestors <tag>         List all tags that are ancestors of a tag
#   check <new> <old>       Check if <old> is an ancestor of <new>
#   tree                    Show a visual tree of model tag relationships
#   info <tag>              Show detailed info about a tag

set -e

MODEL_TAG_PATTERN="${MODEL_TAG_PATTERN:-v[0-9]*}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get all model tags sorted by version number
get_model_tags() {
    git tag -l "$MODEL_TAG_PATTERN" | sort -V
}

# Check if tag1 is an ancestor of tag2
is_ancestor() {
    git merge-base --is-ancestor "$1" "$2" 2>/dev/null
}

# Get the most recent model tag that is an ancestor of the given ref
get_base_tag() {
    local ref="$1"
    local best_tag=""
    local best_depth=999999
    
    for tag in $(get_model_tags); do
        # Skip if same tag
        [[ "$tag" == "$ref" ]] && continue
        
        # Check if tag is an ancestor of ref
        if is_ancestor "$tag" "$ref"; then
            # Count commits between tag and ref (fewer = closer ancestor)
            depth=$(git rev-list --count "$tag".."$ref" 2>/dev/null || echo 999999)
            if [[ $depth -lt $best_depth ]]; then
                best_depth=$depth
                best_tag=$tag
            fi
        fi
    done
    
    echo "$best_tag"
}

# Get all model tags that are ancestors of the given ref
get_ancestor_tags() {
    local ref="$1"
    for tag in $(get_model_tags); do
        [[ "$tag" == "$ref" ]] && continue
        if is_ancestor "$tag" "$ref"; then
            echo "$tag"
        fi
    done
}

# Get all model tags that contain (descend from) the given ref
get_descendant_tags() {
    local ref="$1"
    for tag in $(get_model_tags); do
        [[ "$tag" == "$ref" ]] && continue
        if is_ancestor "$ref" "$tag"; then
            echo "$tag"
        fi
    done
}

cmd_list() {
    echo "Model tags (pattern: $MODEL_TAG_PATTERN):"
    echo "==========================================="
    for tag in $(get_model_tags); do
        local date=$(git log -1 --format="%ci" "$tag" 2>/dev/null | cut -d' ' -f1)
        local subject=$(git log -1 --format="%s" "$tag" 2>/dev/null | head -c 50)
        printf "  %-20s %s  %s\n" "$tag" "$date" "$subject"
    done
}

cmd_base() {
    local ref="$1"
    if [[ -z "$ref" ]]; then
        echo "Usage: $0 base <tag|branch>"
        exit 1
    fi
    
    local base=$(get_base_tag "$ref")
    if [[ -n "$base" ]]; then
        local commits=$(git rev-list --count "$base".."$ref")
        echo -e "Base tag: ${GREEN}$base${NC}"
        echo "Commits since base: $commits"
    else
        echo -e "${YELLOW}No model tag ancestor found${NC}"
        # Fall back to describing from any tag
        local desc=$(git describe --tags --abbrev=0 "$ref" 2>/dev/null || echo "none")
        echo "Nearest tag (any): $desc"
    fi
}

cmd_contains() {
    local ref="$1"
    if [[ -z "$ref" ]]; then
        echo "Usage: $0 contains <tag>"
        exit 1
    fi
    
    echo "Tags that contain (descend from) $ref:"
    echo "========================================"
    local descendants=$(get_descendant_tags "$ref")
    if [[ -n "$descendants" ]]; then
        for tag in $descendants; do
            local commits=$(git rev-list --count "$ref".."$tag")
            printf "  ${GREEN}%-20s${NC} (+%d commits)\n" "$tag" "$commits"
        done
    else
        echo -e "  ${YELLOW}(none)${NC}"
    fi
}

cmd_ancestors() {
    local ref="$1"
    if [[ -z "$ref" ]]; then
        echo "Usage: $0 ancestors <tag>"
        exit 1
    fi
    
    echo "Ancestor tags of $ref:"
    echo "======================="
    local ancestors=$(get_ancestor_tags "$ref")
    if [[ -n "$ancestors" ]]; then
        for tag in $ancestors; do
            local commits=$(git rev-list --count "$tag".."$ref")
            printf "  ${BLUE}%-20s${NC} (-%d commits)\n" "$tag" "$commits"
        done
    else
        echo -e "  ${YELLOW}(none)${NC}"
    fi
}

cmd_check() {
    local new="$1"
    local old="$2"
    if [[ -z "$old" || -z "$new" ]]; then
        echo "Usage: $0 check <new-tag> <old-tag>"
        exit 1
    fi
    
    if is_ancestor "$old" "$new"; then
        local commits=$(git rev-list --count "$old".."$new")
        echo -e "${GREEN}✓ $new contains $old${NC} (+$commits commits)"
        exit 0
    else
        # Check if it's the reverse
        if is_ancestor "$new" "$old"; then
            echo -e "${YELLOW}✗ $new does NOT contain $old${NC} (but $old contains $new)"
        else
            # Find common ancestor
            local merge_base=$(git merge-base "$old" "$new" 2>/dev/null || echo "")
            if [[ -n "$merge_base" ]]; then
                local base_tag=$(git describe --tags --abbrev=0 "$merge_base" 2>/dev/null || echo "$merge_base")
                echo -e "${RED}✗ $new does NOT contain $old${NC}"
                echo "  They diverged from: $base_tag"
            else
                echo -e "${RED}✗ $new does NOT contain $old${NC} (no common ancestor)"
            fi
        fi
        exit 1
    fi
}

cmd_tree() {
    echo "Model Tag Lineage Tree:"
    echo "======================="
    
    # Use a global array for tags so nested function can access it
    _tree_tags=($(get_model_tags))
    
    # Find root tags (tags with no model tag ancestors)
    local roots=()
    for tag in "${_tree_tags[@]}"; do
        local has_ancestor=false
        for other in "${_tree_tags[@]}"; do
            [[ "$tag" == "$other" ]] && continue
            if is_ancestor "$other" "$tag"; then
                has_ancestor=true
                break
            fi
        done
        if ! $has_ancestor; then
            roots+=("$tag")
        fi
    done
    
    # Print tree starting from each root
    _print_tree() {
        local current="$1"
        local prefix="$2"
        local is_last="$3"
        local child_prefix
        
        # Print current node
        if [[ -z "$prefix" ]]; then
            # Root node - no prefix, children get initial indent
            echo "$current"
            child_prefix="  "
        else
            if $is_last; then
                echo "${prefix}└── $current"
                child_prefix="${prefix}    "
            else
                echo "${prefix}├── $current"
                child_prefix="${prefix}│   "
            fi
        fi
        
        # Find direct children (tags whose closest ancestor is current)
        local children=()
        for tag in "${_tree_tags[@]}"; do
            [[ "$tag" == "$current" ]] && continue
            if is_ancestor "$current" "$tag"; then
                # Check if current is the closest ancestor model tag for this tag
                local closest_ancestor="$current"
                local closest_depth=$(git rev-list --count "$current".."$tag" 2>/dev/null || echo 999999)
                
                for other in "${_tree_tags[@]}"; do
                    [[ "$other" == "$current" || "$other" == "$tag" ]] && continue
                    if is_ancestor "$other" "$tag"; then
                        local depth=$(git rev-list --count "$other".."$tag" 2>/dev/null || echo 999999)
                        if [[ $depth -lt $closest_depth ]]; then
                            closest_depth=$depth
                            closest_ancestor="$other"
                        fi
                    fi
                done
                
                # Only add as child if current is the closest ancestor
                if [[ "$closest_ancestor" == "$current" ]]; then
                    children+=("$tag")
                fi
            fi
        done
        
        # Recursively print children
        local child_count=${#children[@]}
        local i=0
        for child in "${children[@]}"; do
            i=$((i + 1))
            local last=false
            [[ $i -eq $child_count ]] && last=true
            _print_tree "$child" "$child_prefix" $last
        done
    }
    
    for root in "${roots[@]}"; do
        _print_tree "$root" "" false
        echo
    done
    
    # Clean up global variable
    unset _tree_tags
}

cmd_info() {
    local ref="$1"
    if [[ -z "$ref" ]]; then
        echo "Usage: $0 info <tag>"
        exit 1
    fi
    
    echo "Tag Information: $ref"
    echo "==========================================="
    
    # Basic info
    local commit=$(git rev-parse "$ref" 2>/dev/null)
    local date=$(git log -1 --format="%ci" "$ref" 2>/dev/null)
    local author=$(git log -1 --format="%an" "$ref" 2>/dev/null)
    local subject=$(git log -1 --format="%s" "$ref" 2>/dev/null)
    
    echo "Commit:  $commit"
    echo "Date:    $date"
    echo "Author:  $author"
    echo "Subject: $subject"
    echo
    
    # Base tag
    local base=$(get_base_tag "$ref")
    if [[ -n "$base" ]]; then
        local base_commits=$(git rev-list --count "$base".."$ref")
        echo -e "Based on: ${GREEN}$base${NC} (+$base_commits commits)"
    else
        echo -e "Based on: ${YELLOW}(no model tag ancestor)${NC}"
    fi
    
    # Ancestors
    echo
    echo "All ancestor model tags:"
    local ancestors=$(get_ancestor_tags "$ref")
    if [[ -n "$ancestors" ]]; then
        for tag in $ancestors; do
            echo "  - $tag"
        done
    else
        echo "  (none)"
    fi
    
    # Descendants
    echo
    echo "Descendant model tags:"
    local descendants=$(get_descendant_tags "$ref")
    if [[ -n "$descendants" ]]; then
        for tag in $descendants; do
            echo "  - $tag"
        done
    else
        echo "  (none)"
    fi
}

cmd_help() {
    cat << EOF
Model Lineage Analysis Tool - Analyze model iteration tag relationships; Assumed invoked using 'make'.

Usage: make model <command> [args]

Commands:
  list                    List all model tags with dates and subjects
  base <tag>              Show the closest ancestor model tag
  contains <tag>          List all tags that descend from this tag
  ancestors <tag>         List all model tags that are ancestors
  check <old> <new>       Check if <new> contains <old>
  tree                    Show a visual tree of tag relationships
  info <tag>              Show detailed info about a tag
  help                    Show this help message

Configuration:
  Set MODEL_TAG_PATTERN environment variable to customize tag matching.
  Default: "[v0-9]*"
  
  Example: make MODEL_TAG_PATTERN="v*" model list

Examples:
  make model check model-22 model-21    # Does model-22 include model-21?
  make model base feature/new-risk      # What model tag is this branch based on?
  make model tree                       # Visualize all model tag relationships
EOF
}

# Main command dispatch
case "${1:-help}" in
    list)       cmd_list ;;
    base)       cmd_base "$2" ;;
    contains)   cmd_contains "$2" ;;
    ancestors)  cmd_ancestors "$2" ;;
    check)      cmd_check "$2" "$3" ;;
    matrix)     cmd_matrix ;;
    tree)       cmd_tree ;;
    info)       cmd_info "$2" ;;
    help|--help|-h) cmd_help ;;
    *)          echo "Unknown command: $1"; cmd_help; exit 1 ;;
esac