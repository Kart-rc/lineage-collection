package shop;

import java.util.List;

public class CustomerController {
    private final CustomerRepository customers;

    public CustomerController(CustomerRepository customers) {
        this.customers = customers;
    }

    public List<Customer> byCity(String city) {
        return customers.findByCity(city);
    }

    public Customer save(Customer customer) {
        return customers.save(customer);
    }
}
