#!/bin/bash

# Initialize variables
models=""
model_runs=""
baseline_model_runs=""

# Parse arguments
while getopts "m:r:b:" opt; do
  case $opt in
    m)
      shift $((OPTIND-2))
      models=()
      while [[ $# -gt 0 ]] && [[ $1 != -* ]]; do
        # Expand wildcards
        for file in $1; do
          if [[ -f "$file" ]]; then
            models+=("$file")
          fi
        done
        shift
      done
      OPTIND=1
      ;;
    r)
      model_runs=$OPTARG
      ;;
    b)
      baseline_model_runs=$OPTARG
      ;;
    *)
      echo "Usage: $0 -m <model_spec_baseline.yaml model_spec2.yaml ...> -r <number_of_runs>"
      exit 1
      ;;
  esac
done

if [ ${#models[@]} -eq 0 ] || [ -z "$model_runs" ] || [ -z "$baseline_model_runs" ]; then
  echo "Error: All arguments -m, -r, and -b are required."
  echo "Usage: $0 -m <model_spec_baseline.yaml model_spec2.yaml ...> -r <number_of_runs> -b <number_of_baseline_runs>"
  echo "Example: $0 -m model*.yaml -r 10 -b 20"
  exit 1
fi

if ! printf '%s\n' "${models[@]}" | grep -q "model_spec_baseline.yaml"; then
  echo "Error: One of the model specs must be 'model_spec_baseline.yaml'."
  exit 1
fi

# Convert to arrays for consistency with rest of script
MODEL_SPECS=("${models[@]}")
MODEL_RUNS="$model_runs"
BASELINE_MODEL_RUNS="$baseline_model_runs"

RESULTS_DIR="profile_$(date +%Y_%m_%d_%H_%M_%S)"

echo ""
echo "Running benchmarks:"
echo "  Model Specs: ${MODEL_SPECS[*]}"
echo "  Runs: $MODEL_RUNS ($BASELINE_MODEL_RUNS for baseline)"
echo "  Results Directory: $RESULTS_DIR"
echo ""

# Create results directory
mkdir -p $RESULTS_DIR

# Initialize results file
echo "model_spec,run,rt_s,mem_mb,gather_results_cumtime,gather_results_percall,gather_results_ncalls,pipeline_call_cumtime,pipeline_call_percall,pipeline_call_ncalls,population_get_cumtime,population_get_percall,population_get_ncalls" > "$RESULTS_DIR/benchmark_results.csv"

for spec in "${MODEL_SPECS[@]}"; do
    echo ""
    echo "Running $spec..."
    echo ""

    model_spec_name=$(basename "$spec" .yaml)
    spec_specific_results_dir="$RESULTS_DIR/$model_spec_name"
    mkdir -p $spec_specific_results_dir
    
    if [[ "$spec" == *"model_spec_baseline.yaml" ]]; then
      num_runs=$BASELINE_MODEL_RUNS
    else
      num_runs=$MODEL_RUNS
    fi

    for run in $(seq 1 $num_runs); do
        echo ""
        echo "Run $run/$num_runs for $spec..."
        echo ""
        
        # Run with memory profiling
        mprof run -CM simulate profile $spec -o $spec_specific_results_dir
        
        # Get the current results directory
        current_results_dir=$(ls -1d $spec_specific_results_dir/*/ | sort | tail -n 1)

        # Get peak memory
        mem_mb=$(echo "$(mprof peak)" | grep -oE '[0-9]+\.[0-9]+' | head -1)
        # Move the mprof file to the curent results dir
        mv mprofile_*.dat "$current_results_dir/"
        
        # Get runtime
        stats_file="$current_results_dir/$model_spec_name.stats"
        stats_file_txt="$stats_file.txt"
        rt_s=$(grep "function calls.*in [0-9.]* seconds" "$stats_file_txt" | grep -oE '[0-9]+\.[0-9]+ seconds' | grep -oE '[0-9]+\.[0-9]+')
        
        # Extract specific function performance metrics
        # NOTE: These grep patterns match function names without line numbers
        gather_results_line=$(grep "results/manager.py:[0-9]*(gather_results)" "$stats_file_txt" | head -1)
        pipeline_call_line=$(grep "values/pipeline.py:[0-9]*(__call__)" "$stats_file_txt" | head -1)
        population_get_line=$(grep "population/population_view.py:[0-9]*(get)" "$stats_file_txt" | head -1)
        
        # Parse cumtime, percall, and ncalls for each function
        gather_results_cumtime=$(echo "$gather_results_line" | awk '{print $4}')
        gather_results_percall=$(echo "$gather_results_line" | awk '{print $5}')
        gather_results_ncalls=$(echo "$gather_results_line" | awk '{if (index($1, "/")) {split($1, arr, "/"); print arr[2]} else {print $1}}')
        
        pipeline_call_cumtime=$(echo "$pipeline_call_line" | awk '{print $4}')
        pipeline_call_percall=$(echo "$pipeline_call_line" | awk '{print $5}')
        pipeline_call_ncalls=$(echo "$pipeline_call_line" | awk '{if (index($1, "/")) {split($1, arr, "/"); print arr[2]} else {print $1}}')
        
        population_get_cumtime=$(echo "$population_get_line" | awk '{print $4}')
        population_get_percall=$(echo "$population_get_line" | awk '{print $5}')
        population_get_ncalls=$(echo "$population_get_line" | awk '{if (index($1, "/")) {split($1, arr, "/"); print arr[2]} else {print $1}}')
        
        # Log results
        echo "$spec,$run,$rt_s,$mem_mb,$gather_results_cumtime,$gather_results_percall,$gather_results_ncalls,$pipeline_call_cumtime,$pipeline_call_percall,$pipeline_call_ncalls,$population_get_cumtime,$population_get_percall,$population_get_ncalls" >> "$RESULTS_DIR/benchmark_results.csv"

        echo ""
        echo "Finished run $run/$num_runs for $spec"
        echo "    Runtime: ${rt_s}s, Peak Memory: ${mem_mb}MB"
        echo ""

    done
done

echo ""
echo "Benchmark complete! Results saved to $RESULTS_DIR/benchmark_results.csv"
echo ""

# # Analyze results
# python3 analyze_benchmarks.py "$RESULTS_DIR/benchmark_results.csv" --baseline "$BASELINE_SPEC"
