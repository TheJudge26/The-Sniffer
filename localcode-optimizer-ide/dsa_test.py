def contains_duplicate(nums):
    for i in range(len(nums)):
        for j in range(len(nums)):
            if i != j and nums[i] == nums[j]:
                print("Duplicate found!")
                return True  
    return False


    